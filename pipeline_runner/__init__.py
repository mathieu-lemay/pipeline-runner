import base64
import functools
import gzip
import logging
import os
import re
import sys
import tarfile
import uuid
from time import time as ts
from typing import Dict, List, Optional, Tuple, Union

import boto3
import click
import docker
from dotenv import dotenv_values, load_dotenv

from .config import config
from .models import Cache, Image, ParallelStep, Pipelines, Step
from .parse import PipelinesFileParser
from .utils import (
    FileStreamer,
    get_artifact_directory,
    get_git_current_branch,
    get_git_current_commit,
    get_local_cache_directory,
    stringify,
)

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s"))

logger = logging.getLogger(__name__)
logger.handlers.append(handler)
logger.setLevel("INFO")

docker_logger = logging.getLogger("docker")
docker_logger.handlers.append(handler)
docker_logger.setLevel("INFO")

handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter("%(message)s"))
output_logger = logging.getLogger("output")
output_logger.handlers.append(handler)
output_logger.setLevel("INFO")


class PipelineRunner:
    def __init__(self, pipeline: str):
        self._pipeline = pipeline
        self._uuid = str(uuid.uuid4())

    def run(self):
        self._load_env_files()

        pipeline, pipelines_definition = self._load_pipeline()

        s = ts()
        exit_code = self._execute_pipeline(pipeline, pipelines_definition)
        logger.info("Pipeline '%s' executed in %.3f.", pipeline.name, ts() - s)

        if exit_code:
            logger.error("Pipeline '%s' failed", pipeline.name)

    @staticmethod
    def _load_env_files():
        logger.debug("Loading .env file (if exists)")
        load_dotenv(override=True)

        for env_file in config.env_files:
            if not os.path.exists(env_file):
                raise ValueError(f"Invalid env file: {env_file}")

            logger.debug("Loading env file: %s", env_file)
            load_dotenv(env_file, override=True)

    def _load_pipeline(self):
        pipelines_definition = PipelinesFileParser(config.pipeline_file).parse()

        pipeline_to_run = pipelines_definition.get_pipeline(self._pipeline)

        if not pipeline_to_run:
            msg = f"Invalid pipeline: {self._pipeline}"
            logger.error(msg)
            logger.info(
                "Available pipelines:\n\t%s", "\n\t".join(sorted(pipelines_definition.get_available_pipelines()))
            )
            raise ValueError(msg)

        return pipeline_to_run, pipelines_definition

    def _execute_pipeline(self, pipeline, definitions):
        for step in pipeline.steps:
            step_uuid = str(uuid.uuid4())
            runner = StepRunnerFactory.get(step, self._uuid, step_uuid, definitions)

            exit_code = runner.run()

            if exit_code:
                return exit_code


class StepRunner:
    def __init__(self, step: Union[Step, ParallelStep], pipeline_uuid: str, step_uuid: str, definitions: Pipelines):
        self._step = step
        self._pipeline_uuid = pipeline_uuid
        self._step_uuid = step_uuid
        self._definitions = definitions

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)
        s = ts()

        image = self._get_image()

        runner = DockerRunner(image, self._pipeline_uuid, self._step_uuid, self._step, self._definitions.caches)

        try:
            exit_code = runner.run(self._step.script)

            runner.collect_artifacts(self._step.artifacts)

            logger.info("Step '%s' executed in %.3fs with exit code: %s", self._step.name, ts() - s, exit_code)

            if exit_code:
                logger.error("Step '%s': FAIL", self._step.name)
        finally:
            runner.close()

        return exit_code

    def _should_run(self):
        if config.selected_steps and self._step.name not in config.selected_steps:
            return False

        return True

    def _get_image(self):
        if self._step.image:
            return self._step.image

        if self._definitions.image:
            return self._definitions.image

        return Image(config.default_image)


class ParallelStepRunner(StepRunner):
    def __init__(self, step: Union[Step, ParallelStep], pipeline_uuid: str, step_uuid: str, definitions: Pipelines):
        super().__init__(step, pipeline_uuid, step_uuid, definitions)

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        return_code = 0
        for s in self._step.steps:
            runner = StepRunnerFactory.get(s, self._pipeline_uuid, self._step_uuid, self._definitions)
            rc = runner.run()
            if rc:
                return_code = rc

        return return_code


class StepRunnerFactory:
    @staticmethod
    def get(step: Union[Step, ParallelStep], pipeline_uuid: str, step_uuid: str, definitions: Pipelines):
        if isinstance(step, ParallelStep):
            return ParallelStepRunner(step, pipeline_uuid, step_uuid, definitions)
        else:
            return StepRunner(step, pipeline_uuid, step_uuid, definitions)


def timing(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        s = ts()
        ret = fn(*args, **kwargs)
        logger.debug("Executed in %.3fs", ts() - s)
        return ret

    return wrapper


class DockerRunner:
    def __init__(self, image: Image, pipeline_uuid: str, step_uuid: str, step: Step, caches: Dict[str, Cache]):
        self._image = image
        self._pipeline_uuid = pipeline_uuid
        self._step_uuid = step_uuid
        self._step = step
        self._caches = caches

        self._client = docker.from_env()
        self._container = None

    def run(self, script: Union[str, List[str]]) -> int:
        try:
            self._setup_build()
            exit_code = self._execute(script)
        except Exception as e:
            logger.exception(f"Error running script in docker container: {e}")
            raise
        else:
            return exit_code

    def close(self):
        self._stop_container()

    def collect_artifacts(self, artifacts: Optional[List[str]]):
        if not artifacts:
            return

        self._save_artifacts(artifacts)

    def _setup_build(self):
        self._pull_image()
        self._start_container()
        self._clone_repository()
        self._load_artifacts()

    @timing
    def _execute(self, command: Union[str, List[str]]) -> int:
        command = stringify(command, sep="\n")

        exit_code = self._run_in_container(command)

        return exit_code

    @timing
    def _stop_container(self):
        if not self._container:
            return

        logger.info("Killing container: %s", self._container.name)
        self._container.kill()

    @timing
    def _pull_image(self):
        logger.info("Pulling image: %s", self._image.name)

        auth_config = self._get_docker_auth_config()
        self._client.images.pull(self._image.name, auth_config=auth_config)

    @timing
    def _start_container(self):
        logger.info("Starting container")

        volumes = self._get_volumes()

        # TODO: Only when needed
        if True:
            volumes["/var/run/docker.sock"] = {"bind": "/var/run/docker.sock"}

        self._container = self._client.containers.run(
            self._image.name,
            name=f"{config.project_slug}-{self._step_uuid}",
            entrypoint="sh",
            tty=True,
            detach=True,
            remove=True,
            working_dir=config.build_dir,
            environment=self._get_env_vars(),
            volumes=volumes,
        )
        logger.debug("Created container: %s", self._container.name)

    @timing
    def _clone_repository(self):
        # GIT_LFS_SKIP_SMUDGE=1 retry 6 git clone --branch="tbd/DRCT-455-enable-build-on-commits-to-trun"
        # --depth 50 https://x-token-auth:$REPOSITORY_OAUTH_ACCESS_TOKEN@bitbucket.org/$BITBUCKET_REPO_FULL_NAME.git
        # $BUILD_DIR

        exit_code = self._run_in_container(
            [
                "GIT_LFS_SKIP_SMUDGE=1",
                "git",
                "clone",
                "--branch",
                get_git_current_branch(),
                "--depth",
                "50",
                "/var/run/workspace",
                "$BUILD_DIR",
            ]
        )

        if exit_code:
            raise Exception("Error cloning repository")

        exit_code = self._run_in_container(["git", "reset", "--hard", "$BITBUCKET_COMMIT"])

        if exit_code:
            raise Exception("Error resetting to HEAD commit")

    def _run_in_container(self, command: Union[str, List[str]], shell=True):
        def wrap_in_shell(cmd):
            if shell:
                return ["sh", "-e", "-c", cmd]
            else:
                return cmd

        command = stringify(command)

        output_logger.info("+ " + command.replace("\n", "\n+ "))

        exit_code, output = self._container.exec_run(wrap_in_shell(command), tty=True)
        logger.debug("Command exited with code: %d", exit_code)

        output_logger.info(output.decode())

        return exit_code

    def _get_docker_auth_config(self):
        if self._image.aws:
            aws_access_key_id = self._image.aws["access-key"]
            aws_secret_access_key = self._image.aws["secret-key"]
            aws_session_token = os.getenv("AWS_SESSION_TOKEN")

            client = boto3.client(
                "ecr",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name="ca-central-1",
            )

            resp = client.get_authorization_token()

            credentials = base64.b64decode(resp["authorizationData"][0]["authorizationToken"]).decode()
            username, password = credentials.split(":", maxsplit=1)

            return {
                "username": username,
                "password": password,
            }

        if self._image.username and self._image.password:
            return {
                "username": self._image.username,
                "password": self._image.password,
            }

        return None

    def _get_env_vars(self):
        env_vars = self._get_pipelines_env_vars()

        env_vars.update(dotenv_values())
        for env_file in config.env_files:
            env_vars.update(dotenv_values(env_file))

        return env_vars

    @staticmethod
    def _get_pipelines_env_vars() -> Dict[str, str]:
        project_slug = config.project_slug
        return {
            "BUILD_DIR": config.build_dir,
            "BITBUCKET_BRANCH": get_git_current_branch(),
            "BITBUCKET_BUILD_NUMBER": 1,
            "BITBUCKET_CLONE_DIR": config.build_dir,
            "BITBUCKET_COMMIT": get_git_current_commit(),
            "BITBUCKET_EXIT_CODE": 0,
            "BITBUCKET_PROJECT_KEY": "PR",
            "BITBUCKET_REPO_FULL_NAME": f"{project_slug}/{project_slug}",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": config.username,
            "BITBUCKET_REPO_OWNER_UUID": config.owner_uuid,
            "BITBUCKET_REPO_SLUG": project_slug,
            "BITBUCKET_REPO_UUID": config.repo_uuid,
            "BITBUCKET_WORKSPACE": project_slug,
            "COMPOSE_DOCKER_CLI_BUILD": 0,
        }

    def _get_volumes(self):
        volumes = {config.project_directory: {"bind": "/var/run/workspace", "mode": "ro"}}

        for cache_name in self._step.caches:
            cache_dirs = self._get_cache_directories(cache_name)
            if not cache_dirs:
                continue

            local_dir, remote_dir = cache_dirs
            remote_dir = self._normalize_home(remote_dir)
            volumes[local_dir] = {"bind": remote_dir}

        return volumes

    def _get_cache_directories(self, cache_name) -> Optional[Tuple[str, str]]:
        if cache_name == "docker":
            return None

        local_dir = get_local_cache_directory(cache_name)
        if cache_name in self._caches:
            remote_dir = self._caches[cache_name].path
        elif cache_name in config.default_caches:
            remote_dir = config.default_caches[cache_name]
        else:
            raise ValueError(f"Invalid cache: {cache_name}")

        return local_dir, remote_dir

    def _normalize_home(self, path: str) -> str:
        home_dir = self._get_home_dir()
        path = re.sub("^~/", home_dir + "/", path)
        path = re.sub("\\$HOME/", home_dir + "/", path)

        return path

    @staticmethod
    def _get_home_dir() -> str:
        return "/root"

    @timing
    def _save_artifacts(self, artifacts: List[str]):
        artifact_file = f"artifacts-{self._step_uuid}.tar.gz"

        logger.info("Saving artifacts")

        self._run_in_container(["tar", "zcf", artifact_file, "-C", config.build_dir] + artifacts)
        data, stat = self._container.get_archive(os.path.join(config.build_dir, artifact_file))
        logger.debug("artifacts stats: %s", stat)

        artifact_directory = get_artifact_directory(self._pipeline_uuid)

        # noinspection PyTypeChecker
        with tarfile.open(fileobj=FileStreamer(data), mode="r|") as tar:
            tar.extractall(artifact_directory)

        logger.info("Artifacts saved to %s", artifact_directory)

    @timing
    def _load_artifacts(self):
        artifact_directory = get_artifact_directory(self._pipeline_uuid)

        for af in os.listdir(artifact_directory):
            with gzip.open(os.path.join(artifact_directory, af), "rb") as f:
                res = self._container.put_archive(config.build_dir, f)
                if not res:
                    raise Exception(f"Error loading artifact: {af}")


@click.command("Pipeline Runner")
@click.argument("pipeline", required=True)
@click.option(
    "-p",
    "--project-directory",
    help="Root directory of the project. Defaults to current directory.",
)
@click.option(
    "-f",
    "--pipeline-file",
    help="File containing the pipeline definitions. Defaults to 'bitbucket-pipelines.yml'",
)
@click.option(
    "-s",
    "--step",
    "steps",
    multiple=True,
    help="Steps to run. If none are specified, they will all be run. Can be specified multiple times.",
)
@click.option(
    "-e",
    "--env-file",
    "env_files",
    multiple=True,
    help="Read in a file of environment variables. Can be specified multiple times.",
)
def main(pipeline, project_directory, pipeline_file, steps, env_files):
    """
    Runs the pipeline PIPELINE.

    PIPELINE is the full path to the pipeline to run. Ex: branches.master
    """

    if project_directory:
        config.project_directory = project_directory

    if pipeline_file:
        config.pipeline_file = pipeline_file

    if steps:
        config.selected_steps = steps

    if env_files:
        config.env_files = env_files

    runner = PipelineRunner(pipeline)
    runner.run()
