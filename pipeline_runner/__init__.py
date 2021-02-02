import logging
import os
import uuid
from time import time as ts
from typing import Optional, Union

import click
from dotenv import load_dotenv

from .artifacts import ArtifactManager
from .cache import CacheManager
from .config import config
from .container import ContainerRunner
from .models import Image, ParallelStep, Pipelines, Step
from .parse import PipelinesFileParser

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s"))

logger = logging.getLogger(__name__)
logger.handlers.append(handler)
logger.setLevel("INFO")

docker_logger = logging.getLogger("docker")
docker_logger.handlers.append(handler)
docker_logger.setLevel("INFO")


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

        self._container_runner = None

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)
        s = ts()

        try:
            image = self._get_image()
            container_name = f"{config.project_slug}-{self._step_uuid}"

            self._container_runner = ContainerRunner(image, container_name)

            self._container_runner.start()

            self._build_setup()

            exit_code = self._container_runner.run_script(self._step.script)

            if exit_code:
                logger.error("Step '%s': FAIL", self._step.name)

            self._build_teardown()
        finally:
            self._container_runner.stop()

        logger.info("Step '%s' executed in %.3fs with exit code: %s", self._step.name, ts() - s, exit_code)

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

    def _build_setup(self):
        logger.info("Build setup: '%s'", self._step.name)
        s = ts()

        am = ArtifactManager(self._container_runner, self._pipeline_uuid, self._step_uuid)
        am.load()

        cm = CacheManager(self._container_runner, self._definitions.caches)
        cm.upload(self._step.caches)

        logger.info("Build setup finished in %.3fs: '%s'", ts() - s, self._step.name)

    def _build_teardown(self):
        logger.info("Build teardown: '%s'", self._step.name)
        s = ts()

        cm = CacheManager(self._container_runner, self._definitions.caches)
        cm.download(self._step.caches)

        am = ArtifactManager(self._container_runner, self._pipeline_uuid, self._step_uuid)
        am.save(self._step.artifacts)

        logger.info("Build teardown finished in %.3fs: '%s'", ts() - s, self._step.name)


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
