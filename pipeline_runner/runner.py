import logging
import os
import sys
from abc import ABC, abstractmethod
from http import HTTPStatus
from time import time as ts

import docker  # type: ignore[import-untyped]
from docker.errors import APIError  # type: ignore[import-untyped]
from docker.models.networks import Network  # type: ignore[import-untyped]

from . import utils
from .artifacts import ArtifactManager
from .cache import CacheManager
from .config import DEFAULT_IMAGE, config
from .container import ContainerRunner
from .context import PipelineRunContext, StepRunContext
from .models import (
    Image,
    ParallelStep,
    Pipe,
    PipelineResult,
    Step,
    StepWrapper,
    Trigger,
    Variable,
)
from .oidc import get_step_oidc_token
from .repository import RepositoryCloner
from .service import ServicesManager

logger = logging.getLogger(__name__)


class PipelineRunRequest:
    def __init__(
        self,
        pipeline_name: str,
        repository_path: str | None = None,
        selected_steps: list[str] | None = None,
        env_files: list[str] | None = None,
    ) -> None:
        self.pipeline_name = pipeline_name
        self.selected_steps = selected_steps or []
        self.env_files = env_files or []
        self.repository_path = os.path.abspath(repository_path or ".")

    @property
    def pipeline_file_path(self) -> str:
        return os.path.join(self.repository_path, "bitbucket-pipelines.yml")


class PipelineRunner:
    def __init__(self, pipeline_run_request: PipelineRunRequest) -> None:
        self._ctx = PipelineRunContext.from_run_request(pipeline_run_request)
        self._pipeline = self._ctx.pipeline

    def run(self) -> PipelineResult:
        logger.info("Running pipeline: %s", self._ctx.pipeline_name)
        logger.debug("Pipeline UUID: %s", self._ctx.pipeline_uuid)

        self._ctx.pipeline_variables = self._ask_for_variables()

        s = ts()
        exit_code = self._execute_pipeline()
        logger.info("Pipeline '%s' executed in %.3fs.", self._ctx.pipeline_name, ts() - s)

        if exit_code:
            logger.error("Pipeline '%s': Failed", self._ctx.pipeline_name)
        else:
            logger.info("Pipeline '%s': Successful", self._ctx.pipeline_name)

        return PipelineResult(exit_code, self._ctx.project_metadata.build_number, self._ctx.pipeline_uuid)

    def _ask_for_variables(self) -> dict[str, str]:
        pipeline_variables = {}
        for var in self._pipeline.get_variables():
            pipeline_variables[var.name] = self._read_user_variable_from_stdin(var)

        return pipeline_variables

    @classmethod
    def _read_user_variable_from_stdin(cls, var: Variable) -> str:
        default = var.default or ""

        if not var.allowed_values:
            value = cls._read_from_stdin(f"Enter value for {var.name} [{default}]") or default
        else:
            prompt = [f"Enter value for {var.name}:"]
            prompt += [f"\t{v}" for v in var.allowed_values]
            prompt.append(f"Choice [{var.default or ''}]")
            value = cls._read_from_stdin("\n".join(prompt)) or default

            if value not in var.allowed_values:
                raise ValueError(f"Invalid value for {var.name}: {value}")

        return value

    @staticmethod
    def _read_from_stdin(prompt: str) -> str:
        if sys.stdin.isatty():
            var = input(f"{prompt}: ")
        else:
            var = sys.stdin.readline()
            if not var:
                raise OSError("Unable to read from stdin")

        return var.rstrip()

    def _execute_pipeline(self) -> int:
        for step in self._pipeline.get_steps():
            runner = StepRunnerFactory.get(step, self._ctx)

            exit_code = runner.run()

            if exit_code:
                return exit_code

        return 0


class BaseStepRunner(ABC):
    @abstractmethod
    def run(self) -> int | None:
        """Run the step."""


class StepRunner(BaseStepRunner):
    def __init__(self, step_run_context: StepRunContext) -> None:
        self._ctx = step_run_context
        self._step = step_run_context.step

        self._docker_client = docker.from_env()
        self._services_manager: ServicesManager | None = None
        self._container_runner: ContainerRunner | None = None

        self._container_name = self._ctx.slug
        self._data_volume_name = f"{self._container_name}-data"
        self._output_logger = utils.get_output_logger(
            self._ctx.pipeline_ctx.get_log_directory(), f"{self._container_name}"
        )

    # TODO: Decomplexify
    # C901: Too complex (>10)
    # PLR0915: Too many statements (>50)
    def run(self) -> int | None:  # noqa: C901, PLR0915
        if not self._should_run():
            logger.info("Skipping step: %s", self._step.name)
            return None

        logger.info("Running step: %s", self._step.name)
        logger.debug("Step ID: %s", self._ctx.step_uuid)

        if self._step.condition is not None:
            logger.warning("Ignoring condition on step: %s", self._step.name)

        if self._step.oidc and not config.oidc.enabled:
            logger.warning("Ignoring OIDC flag on step: %s", self._step.name)

        if self._step.trigger == Trigger.Manual:
            input("Press enter to run step ")

        s = ts()

        network = None

        exit_code: int

        try:
            if "docker" not in self._step.services and self._docker_is_needed():
                logger.debug("Docker service is needed, but wasn't requested. Adding it.")
                self._step.services.append("docker")

            image = self._get_image()
            network = self._get_network()
            environment = self._get_step_env_vars()

            services_manager = ServicesManager(self._ctx, self._data_volume_name)
            self._services_manager = services_manager

            mem_limit = self._get_build_container_memory_limit(services_manager.get_memory_usage())

            container_runner = ContainerRunner(
                self._ctx,
                self._container_name,
                image,
                network.name,
                self._data_volume_name,
                environment,
                self._output_logger,
                mem_limit,
            )
            self._container_runner = container_runner

            container_runner.start()

            services_manager.start_services(f"container:{container_runner.get_container_name()}")

            services = services_manager.get_services_containers()
            container_runner.install_docker_client_if_needed(services)

            self._build_setup()

            exit_code = container_runner.run_script(self._step.script, exec_time=True)

            container_runner.run_script(self._step.after_script, env={"BITBUCKET_EXIT_CODE": exit_code}, exec_time=True)

            if exit_code:
                logger.error("Step '%s': FAIL", self._step.name)

            self._build_teardown(exit_code)
        except Exception:
            logger.exception("Error during pipeline execution")
            exit_code = 1
        finally:
            if self._services_manager:
                self._services_manager.stop_services()

            if self._container_runner:
                self._container_runner.stop()

            if network:
                network.remove()

            volume = next(iter(self._docker_client.volumes.list(filters={"name": self._data_volume_name})), None)
            if volume:
                logger.info("Removing volume: %s", volume.name)
                volume.remove()

        logger.info("Step '%s' executed in %.3fs with exit code: %s", self._step.name, ts() - s, exit_code)

        return exit_code

    def _should_run(self) -> bool:
        if not self._ctx.pipeline_ctx.selected_steps:
            # No step selection means we run everything
            return True

        return self._step.name in self._ctx.pipeline_ctx.selected_steps

    def _get_image(self) -> Image:
        if self._step.image:
            return self._step.image

        if self._ctx.pipeline_ctx.default_image:
            return self._ctx.pipeline_ctx.default_image

        return Image(name=DEFAULT_IMAGE)

    def _get_network(self) -> Network:
        name = f"{self._ctx.pipeline_ctx.project_metadata.slug}-network"
        try:
            bridge_network = self._docker_client.networks.get(name)
        except APIError as e:
            if e.status_code != HTTPStatus.NOT_FOUND:
                raise

            logger.debug("Creating network %s.", name)
            bridge_network = self._create_network(name)
        else:
            logger.debug("Network %s already exists.", name)
        return bridge_network

    def _create_network(self, name: str) -> Network:
        return self._docker_client.networks.create(name, driver="bridge")

    def _get_step_env_vars(self) -> dict[str, str]:
        env_vars = self._get_bitbucket_env_vars()

        if "docker" in self._step.services:
            env_vars["DOCKER_HOST"] = "tcp://localhost:2375"

        env_vars.update(self._ctx.pipeline_ctx.env_vars)
        env_vars.update(self._ctx.pipeline_ctx.pipeline_variables)

        return env_vars

    def _get_bitbucket_env_vars(self) -> dict[str, str]:
        project_slug = self._ctx.pipeline_ctx.project_metadata.slug
        git_branch = self._ctx.pipeline_ctx.repository.get_current_branch()
        git_commit = self._ctx.pipeline_ctx.repository.get_current_commit()

        env_vars: dict[str, str] = {
            "CI": "true",
            "BUILD_DIR": config.build_dir,
            "BITBUCKET_BRANCH": git_branch,
            "BITBUCKET_BUILD_NUMBER": str(self._ctx.pipeline_ctx.project_metadata.build_number),
            "BITBUCKET_PROJECT_KEY": self._ctx.pipeline_ctx.project_metadata.key,
            "BITBUCKET_PROJECT_UUID": str(self._ctx.pipeline_ctx.project_metadata.project_uuid),
            "BITBUCKET_CLONE_DIR": config.build_dir,
            "BITBUCKET_COMMIT": git_commit,
            "BITBUCKET_PIPELINE_UUID": str(self._ctx.pipeline_ctx.pipeline_uuid),
            "BITBUCKET_REPO_FULL_NAME": f"{project_slug}/{project_slug}",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": config.username,
            "BITBUCKET_REPO_OWNER_UUID": str(self._ctx.pipeline_ctx.workspace_metadata.owner_uuid),
            "BITBUCKET_REPO_SLUG": project_slug,
            "BITBUCKET_REPO_UUID": str(self._ctx.pipeline_ctx.project_metadata.repo_uuid),
            "BITBUCKET_STEP_UUID": str(self._ctx.step_uuid),
            "BITBUCKET_WORKSPACE": project_slug,
        }

        if self._ctx.is_parallel():
            env_vars["BITBUCKET_PARALLEL_STEP"] = str(self._ctx.parallel_step_index)
            env_vars["BITBUCKET_PARALLEL_STEP_COUNT"] = str(self._ctx.parallel_step_count)

        if self._step.deployment:
            env_vars["BITBUCKET_DEPLOYMENT_ENVIRONMENT"] = self._step.deployment

        if self._step.oidc and config.oidc.enabled:
            env_vars["BITBUCKET_STEP_OIDC_TOKEN"] = get_step_oidc_token(self._ctx)

        return env_vars

    def _get_build_container_memory_limit(self, services_memory_usage: int) -> int:
        return config.total_memory_limit * self._step.size.as_int() - services_memory_usage

    def _docker_is_needed(self) -> bool:
        return any(i for i in self._step.script + self._step.after_script if isinstance(i, Pipe))

    def _build_setup(self) -> None:
        if not self._services_manager:
            # TODO: Refactor
            raise Exception("called on uninitialized runner")

        logger.info("Build setup: '%s'", self._step.name)
        s = ts()

        self._clone_repository()
        self._upload_artifacts()
        self._upload_caches()

        if self._ctx.pipeline_ctx.pipeline_variables:
            self._output_logger.info("Pipeline Variables:\n")

            for k, v in self._ctx.pipeline_ctx.pipeline_variables.items():
                self._output_logger.info("\t%s: %s\n", k, v)
        self._output_logger.info("\n")

        self._output_logger.info("Images used:\n")
        docker_image = self._docker_client.images.get(self._get_image().name)
        self._output_logger.info("\tbuild: %s@%s\n", docker_image.tags[0].split(":")[0], docker_image.id)
        for name, container in self._services_manager.get_services_containers().items():
            self._output_logger.info("\t%s: %s@%s\n", name, container.image.tags[0].split(":")[0], container.image.id)
        self._output_logger.info("\n")

        logger.info("Build setup finished in %.3fs: '%s'", ts() - s, self._step.name)

    def _upload_artifacts(self) -> None:
        if not self._container_runner:
            # TODO: Refactor
            raise Exception("called on uninitialized runner")

        if not self._step.artifacts.download:
            # Note: Bitbucket Pipelines downloads from a storage, while we upload to the container,
            #       hence the discrepancy between download and upload.
            logger.info("Artifacts downloading disabled, skipping sending artifacts to container")
            return

        am = ArtifactManager(
            self._docker_client,
            self._container_name,
            self._ctx.pipeline_ctx.get_artifact_directory(),
            self._ctx.step_uuid,
        )
        am.upload()

    def _upload_caches(self) -> None:
        if not self._container_runner:
            # TODO: Refactor
            raise Exception("called on uninitialized runner")

        cm = CacheManager(
            self._container_runner,
            self._ctx.pipeline_ctx.repository,
            self._ctx.pipeline_ctx.get_cache_directory(),
            self._ctx.pipeline_ctx.caches,
        )
        cm.upload(self._step.caches)

    def _clone_repository(self) -> None:
        image = self._get_image()

        rc = RepositoryCloner(
            self._ctx,
            self._get_bitbucket_env_vars(),
            image.run_as_user,
            self._container_name,
            self._data_volume_name,
            self._output_logger,
        )
        rc.clone()

    def _build_teardown(self, exit_code: int) -> None:
        logger.info("Build teardown: '%s'", self._step.name)
        s = ts()

        self._download_caches(exit_code)
        self._download_artifacts()
        self._stop_services()

        logger.info("Build teardown finished in %.3fs: '%s'", ts() - s, self._step.name)

    def _download_caches(self, exit_code: int) -> None:
        if not self._container_runner:
            # TODO: Refactor
            raise Exception("called on uninitialized runner")

        if exit_code == 0:
            cm = CacheManager(
                self._container_runner,
                self._ctx.pipeline_ctx.repository,
                self._ctx.pipeline_ctx.get_cache_directory(),
                self._ctx.pipeline_ctx.caches,
            )
            cm.download(self._step.caches)
        else:
            logger.warning("Skipping caches for failed step")

    def _download_artifacts(self) -> None:
        if not self._container_runner:
            # TODO: Refactor
            raise Exception("called on uninitialized runner")

        am = ArtifactManager(
            self._docker_client,
            self._container_name,
            self._ctx.pipeline_ctx.get_artifact_directory(),
            self._ctx.step_uuid,
        )
        am.download(self._step.artifacts)

    def _stop_services(self) -> None:
        # TODO: Remove
        pass


class ParallelStepRunner(BaseStepRunner):
    def __init__(self, parallel_step: ParallelStep, pipeline_run_context: PipelineRunContext) -> None:
        self._parallel_step = parallel_step
        self._pipeline_ctx = pipeline_run_context

    def run(self) -> int | None:
        return_code = 0
        step_count = len(self._parallel_step)

        for idx, s in enumerate(self._parallel_step):
            runner = StepRunnerFactory.get(
                s, self._pipeline_ctx, parallel_step_index=idx, parallel_step_count=step_count
            )
            rc = runner.run()
            if rc:
                return_code = rc

        return return_code


class StepRunnerFactory:
    @staticmethod
    def get(
        step: Step | StepWrapper | ParallelStep,
        pipeline_run_context: PipelineRunContext,
        parallel_step_index: int | None = None,
        parallel_step_count: int | None = None,
    ) -> BaseStepRunner:
        if isinstance(step, ParallelStep):
            return ParallelStepRunner(step, pipeline_run_context)

        s = step.step if isinstance(step, StepWrapper) else step
        return StepRunner(StepRunContext(s, pipeline_run_context, parallel_step_index, parallel_step_count))
