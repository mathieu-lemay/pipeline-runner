import logging
import os
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from dotenv import dotenv_values
from slugify import slugify

from pipeline_runner.errors import InvalidPipelineError

from . import utils
from .config import DEFAULT_CACHES, DEFAULT_SERVICES
from .models import (
    CacheType,
    CloneSettings,
    Image,
    Pipeline,
    ProjectMetadata,
    Repository,
    Service,
    Step,
    WorkspaceMetadata,
)
from .parse import parse_pipeline_file

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .runner import PipelineRunRequest


class PipelineRunContext:
    def __init__(
        self,
        pipeline_name: str,
        pipeline: Pipeline,
        caches: dict[str, CacheType],
        services: dict[str, Service],
        clone_settings: CloneSettings,
        default_image: Image | None,
        workspace_metadata: WorkspaceMetadata,
        project_metadata: ProjectMetadata,
        repository: Repository,
        env_vars: dict[str, str] | None = None,
        selected_steps: list[str] | None = None,
    ) -> None:
        self.pipeline_name = pipeline_name
        self.pipeline = pipeline
        self.caches = self._merge_default_caches(caches)
        self.services = self._merge_default_services(services)
        self.clone_settings = clone_settings
        self.default_image = default_image
        self.workspace_metadata = workspace_metadata
        self.project_metadata = project_metadata
        self.repository = repository
        self.env_vars = env_vars or {}
        self.selected_steps = selected_steps or []

        self.pipeline_uuid = uuid.uuid4()
        self.pipeline_variables: dict[str, str] = {}

        self._data_directory = self.get_pipeline_data_directory()
        self._cache_directory = utils.get_project_cache_directory(project_metadata.path_slug)

    @classmethod
    def from_run_request(cls, req: "PipelineRunRequest") -> "PipelineRunContext":
        env_vars = cls._load_env_vars(req.env_files)
        spec = parse_pipeline_file(req.pipeline_file_path)
        spec.expand_env_vars(env_vars)

        pipeline_name = req.pipeline_name
        pipeline_to_run = spec.get_pipeline(pipeline_name)

        if not pipeline_to_run:
            valid_pipelines = sorted(spec.get_available_pipelines())
            raise InvalidPipelineError(pipeline_name, valid_pipelines)

        workspace_meta = WorkspaceMetadata.load_from_file(req.repository_path)
        project_meta = ProjectMetadata.load_from_file(req.repository_path)
        repository = Repository(req.repository_path)

        return PipelineRunContext(
            pipeline_name,
            pipeline_to_run,
            spec.caches,
            spec.services,
            spec.clone_settings,
            spec.image,
            workspace_meta,
            project_meta,
            repository,
            env_vars,
            req.selected_steps,
        )

    @staticmethod
    def _load_env_vars(env_files: list[str]) -> dict[str, str]:
        envvars: dict[str, str | None] = {}
        # TODO: Load env file in the repo if exists
        logger.debug("Loading .env file (if exists)")
        envvars.update(dotenv_values(".env"))

        for env_file in env_files:
            if not os.path.exists(env_file):
                raise ValueError(f"Invalid env file: {env_file}")

            logger.debug("Loading env file: %s", env_file)
            envvars.update(dotenv_values(env_file))

        sanitized_env_vars = {k: v or "" for k, v in envvars.items()}

        os.environ.update(sanitized_env_vars)

        return sanitized_env_vars

    @staticmethod
    def _merge_default_services(services: dict[str, Service]) -> dict[str, Service]:
        for name, definition in DEFAULT_SERVICES.items():
            default_service = Service.model_validate(definition)

            if name in services:
                service = services[name]
                service.image = service.image or default_service.image
                service.variables = service.variables or default_service.variables
                service.memory = service.memory or default_service.memory
            else:
                services[name] = default_service

        return services

    @staticmethod
    def _merge_default_caches(caches: Mapping[str, CacheType]) -> dict[str, CacheType]:
        all_caches = DEFAULT_CACHES.copy()
        all_caches.update(caches)

        return all_caches

    def get_log_directory(self) -> str:
        return utils.ensure_directory(os.path.join(self._data_directory, "logs"))

    def get_artifact_directory(self) -> str:
        return utils.ensure_directory(os.path.join(self._data_directory, "artifacts"))

    def get_cache_directory(self) -> str:
        return utils.ensure_directory(os.path.join(self._cache_directory, "caches"))

    def get_pipeline_data_directory(self) -> str:
        project_data_dir = utils.get_project_data_directory(self.project_metadata.path_slug)
        pipeline_id = f"{self.project_metadata.build_number}-{self.pipeline_uuid}"

        return os.path.join(project_data_dir, "pipelines", pipeline_id)


class StepRunContext:
    def __init__(
        self,
        step: Step,
        pipeline_run_context: PipelineRunContext,
        parallel_step_index: int | None = None,
        parallel_step_count: int | None = None,
    ) -> None:
        self.step = step
        self.pipeline_ctx = pipeline_run_context
        self.slug = f"{pipeline_run_context.project_metadata.path_slug}-step-{slugify(step.name)}"

        self.step_uuid = uuid.uuid4()

        if (parallel_step_index is None) != (parallel_step_count is None):
            raise ValueError("`parallel_step_index` and `parallel_step_count` must be both defined or both undefined")

        self.parallel_step_index = parallel_step_index
        self.parallel_step_count = parallel_step_count

    def is_parallel(self) -> bool:
        return bool(self.parallel_step_count)
