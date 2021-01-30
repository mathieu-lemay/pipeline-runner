import base64
import functools
import logging
import os
import sys
import uuid
from time import time as ts
from typing import Dict, List, Optional, Union

import boto3
import click
import docker
from dotenv import dotenv_values, load_dotenv
from slugify import slugify

from .config import Config
from .models import Image, ParallelStep, Pipelines, Step
from .parse import PipelinesFileParser
from .utils import get_git_current_branch, get_git_current_commit, stringify

conf = Config()

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

    def run(self):
        self._load_env_files()

        pipelines_definition = PipelinesFileParser(conf.pipeline_file).parse()

        pipeline_to_run = pipelines_definition.get_pipeline(self._pipeline)

        if not pipeline_to_run:
            raise ValueError(f"Invalid pipeline: {self._pipeline}")

        self._execute_pipeline(pipeline_to_run, pipelines_definition)

    @staticmethod
    def _load_env_files():
        logger.debug("Loading .env file (if exists)")
        load_dotenv(override=True)

        for env_file in conf.env_files:
            if not os.path.exists(env_file):
                raise ValueError(f"Invalid env file: {env_file}")

            logger.debug("Loading env file: %s", env_file)
            load_dotenv(env_file, override=True)

    def _execute_pipeline(self, pipeline, definitions):
        return_code = 0

        for step in pipeline.steps:
            runner = StepRunnerFactory.get(step, definitions)

            start = ts()
            return_code = runner.run()
            logger.info("Step '%s' executed in %.3fs. ReturnCode: %s", step.name, ts() - start, return_code)

            if return_code:
                logger.error("Step '%s': FAIL", step.name)
                return return_code


class StepRunner:
    def __init__(self, step: Union[Step, ParallelStep], definitions: Pipelines):
        self._step = step
        self._definitions = definitions

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)

        image = self._get_image()

        runner = DockerRunner(image)
        exit_code = runner.run(self._step.script)

        return exit_code

    def _should_run(self):
        if conf.selected_steps and self._step.name not in conf.selected_steps:
            return False

        return True

    def _get_image(self):
        if self._step.image:
            return self._step.image

        if self._definitions.image:
            return self._definitions.image

        return Image(conf.default_image)


class ParallelStepRunner(StepRunner):
    def __init__(self, step: Union[Step, ParallelStep], definitions: Pipelines):
        super().__init__(step, definitions)

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        return_code = 0
        for s in self._step.steps:
            runner = StepRunnerFactory.get(s, self._definitions)
            rc = runner.run()
            if rc:
                return_code = rc

        return return_code


class StepRunnerFactory:
    @staticmethod
    def get(step: Union[Step, ParallelStep], definitions: Pipelines):
        if isinstance(step, ParallelStep):
            return ParallelStepRunner(step, definitions)
        else:
            return StepRunner(step, definitions)


def timing(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        s = ts()
        ret = fn(*args, **kwargs)
        logger.info("Executed in %.3fs", ts() - s)
        return ret

    return wrapper


class DockerRunner:
    def __init__(self, image: Image, user: Optional[str] = None):
        self._image = image
        self._user = user

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
        finally:
            self._stop_container()

    def _setup_build(self):
        self._pull_image()
        self._start_container()
        self._clone_repository()

    @timing
    def _execute(self, command: Union[str, List[str]]) -> int:
        command = stringify(command, sep="\n")

        exit_code = self._run_in_container(command)

        return exit_code

    @timing
    def _stop_container(self):
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
        self._container = self._client.containers.run(
            self._image.name,
            name=slugify(f"{os.path.basename(conf.project_directory)}-{uuid.uuid4()}"),
            entrypoint="sh",
            tty=True,
            detach=True,
            remove=True,
            working_dir=conf.build_dir,
            environment=self._get_env_vars(),
            volumes={conf.project_directory: {"bind": "/var/run/workspace", "mode": "ro"}},
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

    def _run_in_container(self, command: Union[str, List[str]]):
        def wrap_in_shell(cmd):
            return ["sh", "-e", "-c", cmd]

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
        for env_file in conf.env_files:
            env_vars.update(dotenv_values(env_file))

        return env_vars

    @staticmethod
    def _get_pipelines_env_vars() -> Dict[str, str]:
        project_slug = conf.project_slug
        return {
            "BUILD_DIR": conf.build_dir,
            "BITBUCKET_BRANCH": get_git_current_branch(),
            "BITBUCKET_BUILD_NUMBER": 1,
            "BITBUCKET_CLONE_DIR": conf.build_dir,
            "BITBUCKET_COMMIT": get_git_current_commit(),
            "BITBUCKET_EXIT_CODE": 0,
            "BITBUCKET_PROJECT_KEY": "PR",
            "BITBUCKET_REPO_FULL_NAME": f"{project_slug}/{project_slug}",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": conf.username,
            "BITBUCKET_REPO_OWNER_UUID": conf.owner_uuid,
            "BITBUCKET_REPO_SLUG": project_slug,
            "BITBUCKET_REPO_UUID": conf.repo_uuid,
            "BITBUCKET_WORKSPACE": project_slug,
        }


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
        conf.project_directory = project_directory

    if pipeline_file:
        conf.pipeline_file = pipeline_file

    if steps:
        conf.selected_steps = steps

    if env_files:
        conf.env_files = env_files

    runner = PipelineRunner(pipeline)
    runner.run()
