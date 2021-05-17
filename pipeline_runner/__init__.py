import logging
import os
from time import time as ts
from typing import Dict, List, Optional

import docker

from . import utils
from .artifacts import ArtifactManager
from .cache import CacheManager
from .config import config
from .container import ContainerRunner
from .context import PipelineRunContext, StepRunContext
from .models import Image, ParallelStep, PipelineResult, Trigger
from .repository import RepositoryCloner
from .service import ServicesManager

logger = logging.getLogger(__name__)


class PipelineRunRequest:
    def __init__(
        self,
        pipeline_name: str,
        repository_path: Optional[str] = None,
        selected_steps: List[str] = None,
        env_files: List[str] = None,
    ):
        self.pipeline_name = pipeline_name
        self.selected_steps = selected_steps or []
        self.env_files = env_files or []
        self.repository_path = os.path.abspath(repository_path or ".")

    @property
    def pipeline_file_path(self) -> str:
        return os.path.join(self.repository_path, "bitbucket-pipelines.yml")


class PipelineRunner:
    def __init__(self, pipeline_run_request: PipelineRunRequest):
        self._ctx = PipelineRunContext.from_run_request(pipeline_run_request)
        self._pipeline = self._ctx.pipeline

    def run(self) -> PipelineResult:
        logger.info("Running pipeline: %s", self._pipeline.name)
        logger.debug("Pipeline UUID: %s", self._ctx.pipeline_uuid)

        self._ask_for_variables()

        s = ts()
        exit_code = self._execute_pipeline()
        logger.info("Pipeline '%s' executed in %.3fs.", self._pipeline.name, ts() - s)

        if exit_code:
            logger.error("Pipeline '%s': Failed", self._pipeline.name)
        else:
            logger.info("Pipeline '%s': Successful", self._pipeline.name)

        return PipelineResult(exit_code, self._ctx.build_number, self._ctx.pipeline_uuid)

    def _ask_for_variables(self):
        for varname in self._pipeline.variables:
            self._pipeline.variables[varname] = input(f"Enter value for {varname}: ")

    def _execute_pipeline(self):
        for step in self._pipeline.steps:
            runner = StepRunnerFactory.get(StepRunContext(step, self._ctx))

            exit_code = runner.run()

            if exit_code:
                return exit_code

        return 0


class StepRunner:
    def __init__(self, step_run_context: StepRunContext):
        self._ctx = step_run_context
        self._step = step_run_context.step

        self._docker_client = docker.from_env()
        self._services_manager = None
        self._container_runner = None

        self._container_name = self._ctx.slug
        self._data_volume_name = f"{self._container_name}-data"
        self._output_logger = utils.get_output_logger(
            self._ctx.pipeline_ctx.get_log_directory(), f"{self._container_name}"
        )

    def run(self) -> Optional[int]:
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return

        logger.info("Running step: %s", self._step.name)
        logger.debug("Step ID: %s", self._ctx.step_uuid)

        if self._step.trigger == Trigger.Manual:
            input("Press enter to run step ")

        s = ts()

        try:
            image = self._get_image()

            self._services_manager = ServicesManager(
                self._step.services,
                self._ctx.pipeline_ctx.services,
                self._step.size,
                self._data_volume_name,
                self._ctx.pipeline_ctx.repository.slug,
                self._ctx.pipeline_ctx.get_pipeline_cache_directory(),
            )
            self._services_manager.start_services()

            environment = self._get_step_env_vars()
            services_names = self._services_manager.get_services_names()
            mem_limit = self._get_build_container_memory_limit(self._services_manager.get_memory_usage())

            self._container_runner = ContainerRunner(
                self._container_name,
                image,
                self._ctx.pipeline_ctx.repository.path,
                self._data_volume_name,
                environment,
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
        if self._ctx.pipeline_ctx.selected_steps and self._step.name not in self._ctx.pipeline_ctx.selected_steps:
            return False

        return True

    def _get_image(self):
        if self._step.image:
            return self._step.image

        if self._ctx.pipeline_ctx.default_image:
            return self._ctx.pipeline_ctx.default_image

        return Image(config.default_image)

    def _get_step_env_vars(self) -> Dict[str, str]:
        env_vars = self._get_bitbucket_env_vars()

        if "docker" in self._step.services:
            env_vars["DOCKER_HOST"] = "tcp://localhost:2375"

        env_vars.update(self._ctx.pipeline_ctx.env_vars)
        env_vars.update(self._ctx.pipeline_ctx.pipeline.variables)

        return env_vars

    def _get_bitbucket_env_vars(self) -> Dict[str, str]:
        repo_slug = self._ctx.pipeline_ctx.repository.slug
        git_branch = self._ctx.pipeline_ctx.repository.get_current_branch()
        git_commit = self._ctx.pipeline_ctx.repository.get_current_commit()

        env_vars = {
            "CI": "true",
            "BUILD_DIR": config.build_dir,
            "BITBUCKET_BRANCH": git_branch,
            "BITBUCKET_BUILD_NUMBER": self._ctx.pipeline_ctx.build_number,
            "BITBUCKET_CLONE_DIR": config.build_dir,
            "BITBUCKET_COMMIT": git_commit,
            "BITBUCKET_REPO_FULL_NAME": f"{repo_slug}/{repo_slug}",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": config.username,
            "BITBUCKET_REPO_OWNER_UUID": config.owner_uuid,
            "BITBUCKET_REPO_SLUG": repo_slug,
            "BITBUCKET_REPO_UUID": config.repo_uuid,
            "BITBUCKET_WORKSPACE": repo_slug,
        }

        if self._step.deployment:
            env_vars["BITBUCKET_DEPLOYMENT_ENVIRONMENT"] = self._step.deployment

        return env_vars

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
        am = ArtifactManager(
            self._container_runner, self._ctx.pipeline_ctx.get_artifact_directory(), self._ctx.step_uuid
        )
        am.upload()

    def _upload_caches(self):
        cm = CacheManager(
            self._container_runner, self._ctx.pipeline_ctx.get_pipeline_cache_directory(), self._ctx.pipeline_ctx.caches
        )
        cm.upload(self._step.caches)

    def _clone_repository(self):
        image = self._get_image()

        rc = RepositoryCloner(
            self._ctx.pipeline_ctx.repository,
            self._step.clone_settings,
            self._ctx.pipeline_ctx.clone_settings,
            self._get_bitbucket_env_vars(),
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
            cm = CacheManager(
                self._container_runner,
                self._ctx.pipeline_ctx.get_pipeline_cache_directory(),
                self._ctx.pipeline_ctx.caches,
            )
            cm.download(self._step.caches)
        else:
            logger.warning("Skipping caches for failed step")

    def _download_artifacts(self):
        am = ArtifactManager(
            self._container_runner, self._ctx.pipeline_ctx.get_artifact_directory(), self._ctx.step_uuid
        )
        am.download(self._step.artifacts)

    def _stop_services(self):
        pass


class ParallelStepRunner:
    def __init__(self, step_run_context: StepRunContext):
        self._ctx = step_run_context

    def run(self) -> Optional[int]:
        return_code = 0
        for s in self._ctx.step.steps:
            runner = StepRunnerFactory.get(StepRunContext(s, self._ctx.pipeline_ctx))
            rc = runner.run()
            if rc:
                return_code = rc

        return return_code


class StepRunnerFactory:
    @staticmethod
    def get(step_run_context: StepRunContext):
        if isinstance(step_run_context.step, ParallelStep):
            return ParallelStepRunner(step_run_context)
        else:
            return StepRunner(step_run_context)
