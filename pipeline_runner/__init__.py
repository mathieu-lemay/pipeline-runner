import base64
import logging
from typing import Dict, List, Optional, Union

import boto3
import click
import docker

from .models import Image, ParallelStep, Pipelines, Step
from .parse import PipelinesFileParser
from .utils import DEFAULT_IMAGE

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s"))

logger = logging.getLogger(__name__)
logger.handlers.append(handler)
logger.setLevel("INFO")

docker_logger = logging.getLogger("docker")
docker_logger.handlers.append(handler)
docker_logger.setLevel("DEBUG")


class RunnerContext:
    def __init__(self, selected_steps: List[str], env_files: List[str]):
        self.selected_steps = selected_steps
        self.env_files = env_files


class PipelineRunner:
    def __init__(self, pipeline: str, ctx: RunnerContext):
        self._pipeline = pipeline
        self._ctx = ctx

    def run(self):
        pipelines_definition = PipelinesFileParser("bitbucket-pipelines.yml").parse()

        pipeline_to_run = pipelines_definition.get_pipeline(self._pipeline)

        if not pipeline_to_run:
            raise ValueError(f"Invalid pipeline: {self._pipeline}")

        self._execute_pipeline(pipeline_to_run, pipelines_definition)

    def _execute_pipeline(self, pipeline, definitions):
        for step in pipeline.steps:
            PipelineStepRunner(step, definitions, self._ctx).run()


class PipelineStepRunner:
    def __init__(self, step: Union[Step, ParallelStep], definitions: Pipelines, ctx: RunnerContext):
        self._step = step
        self._definitions = definitions
        self._ctx = ctx

    def run(self):
        if isinstance(self._step, ParallelStep):
            # TODO: Real parallel
            for s in self._step.steps:
                PipelineStepRunner(s, self._definitions, self._ctx).run()

            return

        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)

        image = self._get_image()

        runner = DockerRunner(image, None, self._ctx.env_files)
        runner.start_container()

    def _get_image(self):
        if self._step.image:
            return self._step.image

        if self._definitions.image:
            return self._definitions.image

        return Image(DEFAULT_IMAGE)

    def _should_run(self):
        if self._ctx.selected_steps and self._step.name not in self._ctx.selected_steps:
            return False

        return True


class DockerRunner:
    def __init__(self, image: Image, user: Optional[str], env_files: List[str]):
        self._image = image
        self._user = user
        self._env_files = env_files

        self._client = docker.from_env()

    def start_container(self):
        self._pull_image()

    def _pull_image(self):
        logger.info("Refreshing image: %s", self._image.name)

        auth_config = self._get_docker_auth_config()

        logger.info("Getting image: %s", self._image.name)
        self._client.images.pull(self._image.name, auth_config=auth_config)

    def _get_docker_auth_config(self):
        if self._image.aws:
            client = boto3.client(
                "ecr",
                aws_access_key_id=self._image.aws["access-key"],
                aws_secret_access_key=self._image.aws["secret-key"],
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

    def _build_docker_run_command_line(self) -> [str]:
        return [""]

    def _get_pipelines_env_vars(self) -> Dict[str, str]:
        return {
            "BITBUCKET_BRANCH": "${BITBUCKET_BRANCH}",
            "BITBUCKET_BUILD_NUMBER": "${BITBUCKET_BUILD_NUMBER}",
            "BITBUCKET_CLONE_DIR": "/pipeline",
            "BITBUCKET_COMMIT": "${BITBUCKET_COMMIT}",
            "BITBUCKET_EXIT_CODE": "${BITBUCKET_EXIT_CODE}",
            "BITBUCKET_PROJECT_KEY": "PR",
            "BITBUCKET_REPO_FULL_NAME": "pipeline-runner/pipeline-runner",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": "mathieu-lemay",
            "BITBUCKET_REPO_OWNER_UUID": "{9c7b13eb-f607-4d0b-a0e8-7c87d8a994f0}",
            "BITBUCKET_REPO_SLUG": "pipeline-runner",
            "BITBUCKET_REPO_UUID": "{bcc6cd90-8b08-499c-b739-38ced5a97c3e}",
            "BITBUCKET_WORKSPACE": "pipeline-runner",
        }


@click.command("Pipeline Runner")
@click.argument("pipeline", required=True)
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
def main(pipeline, steps, env_files):
    """
    Runs the pipeline PIPELINE.

    PIPELINE is the full path to the pipeline to run. Ex: branches.master
    """

    ctx = RunnerContext(steps, env_files)
    PipelineRunner(pipeline, ctx).run()
