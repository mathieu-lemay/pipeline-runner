import logging
import os
from time import time as ts
from typing import Optional, Union

import docker
from dotenv import load_dotenv
from slugify import slugify

from . import utils
from .artifacts import ArtifactManager
from .cache import CacheManager
from .config import config
from .container import ContainerRunner
from .models import Image, ParallelStep, Pipeline, PipelineResult, Pipelines, Step
from .parse import PipelinesFileParser
from .repository import RepositoryCloner
from .service import ServicesManager

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, pipeline_name: str):
        self._pipeline_name = pipeline_name

    def run(self) -> PipelineResult:
        self._load_env_files()

        pipeline, pipelines_definition = self._load_pipeline()

        logger.info("Running pipeline: %s", pipeline.name)
        logger.debug("Pipeline ID: %s", pipeline.uuid)

        self._ask_for_variables(pipeline)

        s = ts()
        exit_code = self._execute_pipeline(pipeline, pipelines_definition)
        logger.info("Pipeline '%s' executed in %.3fs.", pipeline.name, ts() - s)

        if exit_code:
            logger.error("Pipeline '%s': Failed", pipeline.name)
        else:
            logger.info("Pipeline '%s': Successful", pipeline.name)

        return PipelineResult(exit_code, pipeline.number, pipeline.uuid)

    @staticmethod
    def _load_env_files():
        logger.debug("Loading .env file (if exists)")
        load_dotenv(".env", override=True)

        for env_file in config.env_files:
            if not os.path.exists(env_file):
                raise ValueError(f"Invalid env file: {env_file}")

            logger.debug("Loading env file: %s", env_file)
            load_dotenv(env_file, override=True)

    def _load_pipeline(self):
        pipelines_definition = PipelinesFileParser(config.pipeline_file).parse()

        pipeline_to_run = pipelines_definition.get_pipeline(self._pipeline_name)

        if not pipeline_to_run:
            msg = f"Invalid pipeline: {self._pipeline_name}"
            logger.error(msg)
            logger.info(
                "Available pipelines:\n\t%s", "\n\t".join(sorted(pipelines_definition.get_available_pipelines()))
            )
            raise ValueError(msg)

        pipeline_to_run.number = self._get_build_number()

        return pipeline_to_run, pipelines_definition

    @staticmethod
    def _ask_for_variables(pipeline: Pipeline):
        for varname in pipeline.variables:
            value = None

            while not value:
                value = input(f"Enter value for {varname}: ")

            pipeline.variables[varname] = value

    @staticmethod
    def _execute_pipeline(pipeline, definitions):
        for step in pipeline.steps:
            runner = StepRunnerFactory.get(step, pipeline, definitions)

            exit_code = runner.run()

            if exit_code:
                return exit_code

        return 0

    @staticmethod
    def _get_build_number():
        if config.bitbucket_build_number:
            return config.bitbucket_build_number

        pi = utils.load_project_pipelines_info()
        pi.build_number += 1

        utils.save_project_pipelines_info(pi)

        return pi.build_number


class StepRunner:
    def __init__(self, step: Step, pipeline: Pipeline, definitions: Pipelines):
        self._step = step
        self._pipeline = pipeline
        self._definitions = definitions

        self._docker_client = docker.from_env()
        self._services_manager = None
        self._container_runner = None

        self._container_name = f"{config.project_slug}-step-{slugify(self._step.name)}"
        self._data_volume_name = f"{self._container_name}-data"
        self._output_logger = utils.get_output_logger(self._pipeline, f"{self._container_name}")

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)
        logger.debug("Step ID: %s", self._step.uuid)

        s = ts()

        try:
            image = self._get_image()

            self._services_manager = ServicesManager(
                self._step.services, self._definitions.services, self._step.size, self._data_volume_name
            )
            self._services_manager.start_services()

            services_names = self._services_manager.get_services_names()
            mem_limit = self._get_build_container_memory_limit(self._services_manager.get_memory_usage())

            self._container_runner = ContainerRunner(
                self._pipeline,
                self._step,
                image,
                self._container_name,
                self._data_volume_name,
                self._output_logger,
                mem_limit,
                services_names,
            )
            self._container_runner.start()

            self._build_setup()

            exit_code = self._container_runner.run_script(self._step.script, exec_time=True)

            self._container_runner.run_script(
                self._step.after_script, env={"BITBUCKET_EXIT_CODE": exit_code}, exec_time=True
            )

            if exit_code:
                logger.error("Step '%s': FAIL", self._step.name)

            self._build_teardown(exit_code)
        finally:
            if self._container_runner:
                self._container_runner.stop()

            if self._services_manager:
                self._services_manager.stop_services()

            volume = next(iter(self._docker_client.volumes.list(filters={"name": self._data_volume_name})), None)
            if volume:
                logger.info("Removing volume: %s", volume.name)
                volume.remove()

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

    def _get_build_container_memory_limit(self, services_memory_usage: int) -> int:
        return config.total_memory_limit * self._step.size - services_memory_usage

    def _build_setup(self):
        logger.info("Build setup: '%s'", self._step.name)
        s = ts()

        self._clone_repository()
        self._upload_artifacts()
        self._upload_caches()

        logger.info("Build setup finished in %.3fs: '%s'", ts() - s, self._step.name)

    def _upload_artifacts(self):
        am = ArtifactManager(self._container_runner, self._pipeline, self._step)
        am.upload()

    def _upload_caches(self):
        cm = CacheManager(self._container_runner, self._definitions.caches)
        cm.upload(self._step.caches)

    def _clone_repository(self):
        image = self._get_image()

        rc = RepositoryCloner(
            self._pipeline,
            self._step,
            self._definitions,
            image.run_as_user,
            self._container_name,
            self._data_volume_name,
            self._output_logger,
        )
        rc.clone()

    def _build_teardown(self, exit_code):
        logger.info("Build teardown: '%s'", self._step.name)
        s = ts()

        self._download_caches(exit_code)
        self._download_artifacts()
        self._stop_services()

        logger.info("Build teardown finished in %.3fs: '%s'", ts() - s, self._step.name)

    def _download_caches(self, exit_code):
        if exit_code == 0:
            cm = CacheManager(self._container_runner, self._definitions.caches)
            cm.download(self._step.caches)
        else:
            logger.warning("Skipping caches for failed step")

    def _download_artifacts(self):
        am = ArtifactManager(self._container_runner, self._pipeline, self._step)
        am.download(self._step.artifacts)

    def _stop_services(self):
        pass


class ParallelStepRunner:
    def __init__(self, step: ParallelStep, pipeline: Pipeline, definitions: Pipelines):
        self._step = step
        self._pipeline = pipeline
        self._definitions = definitions

    def run(self) -> Optional[int]:
        return_code = 0
        for s in self._step.steps:
            runner = StepRunnerFactory.get(s, self._pipeline, self._definitions)
            rc = runner.run()
            if rc:
                return_code = rc

        return return_code


class StepRunnerFactory:
    @staticmethod
    def get(step: Union[Step, ParallelStep], pipeline: Pipeline, definitions: Pipelines):
        if isinstance(step, ParallelStep):
            return ParallelStepRunner(step, pipeline, definitions)
        else:
            return StepRunner(step, pipeline, definitions)
