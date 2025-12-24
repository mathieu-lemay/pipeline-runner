import logging
import os
import uuid
from dataclasses import dataclass, field
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
    Options,
    Pipeline,
    ProjectMetadata,
    Repository,
    Service,
    Step,
    WorkspaceMetadata,
)
from .parse import parse_pipeline_file
from .utils import coalesce

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .runner import PipelineRunRequest


@dataclass(kw_only=True)
class PipelineRunContext:
    pipeline_name: str
    pipeline: Pipeline
    caches: dict[str, CacheType]
    services: dict[str, Service]
    clone_settings: CloneSettings
    options: Options
    default_image: Image | None = field(default=None)
    workspace_metadata: WorkspaceMetadata
    project_metadata: ProjectMetadata
    repository: Repository
    env_vars: dict[str, str] = field(default_factory=dict)
    selected_steps: list[str] = field(default_factory=list)
    selected_stages: list[str] = field(default_factory=list)

    def __post_init__(
        self,
    ) -> None:
        self._merge_default_caches()
        self._merge_default_services()

        self.pipeline_uuid = uuid.uuid4()
        self.pipeline_variables: dict[str, str] = {}

        self._data_directory = self.get_pipeline_data_directory()
        self._cache_directory = utils.get_project_cache_directory(self.project_metadata.path_slug)

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
            pipeline_name=pipeline_name,
            pipeline=pipeline_to_run,
            caches=spec.caches,
            services=spec.services,
            clone_settings=spec.clone_settings,
            options=spec.options,
            default_image=spec.image,
            workspace_metadata=workspace_meta,
            project_metadata=project_meta,
            repository=repository,
            env_vars=env_vars,
            selected_steps=req.selected_steps,
            selected_stages=req.selected_stages,
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

    def _merge_default_services(self) -> None:
        for name, definition in DEFAULT_SERVICES.items():
            default_service = Service.model_validate(definition)

            if name in self.services:
                service = self.services[name]
                service.image = service.image or default_service.image
                service.variables = service.variables or default_service.variables
                service.memory = service.memory or default_service.memory
            else:
                self.services[name] = default_service

    def _merge_default_caches(self) -> None:
        all_caches = DEFAULT_CACHES.copy()
        all_caches.update(self.caches)

        self.caches = all_caches

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


@dataclass
class StepRunContext:
    step: Step
    pipeline_ctx: PipelineRunContext
    parallel_step_index: int | None = None
    parallel_step_count: int | None = None

    def __post_init__(self) -> None:
        self.slug = f"{self.pipeline_ctx.project_metadata.path_slug}-step-{slugify(self.step.name)}"
        self.step_uuid = uuid.uuid4()

        # Merge global options in step values
        self.step.size = coalesce(self.step.size, self.pipeline_ctx.options.size)
        self.step.max_time = coalesce(self.step.max_time, self.pipeline_ctx.options.max_time)
        self.step.runtime = coalesce(self.step.runtime, self.pipeline_ctx.options.runtime)

        if (self.parallel_step_index is None) != (self.parallel_step_count is None):
            raise ValueError("`parallel_step_index` and `parallel_step_count` must be both defined or both undefined")

        if self.pipeline_ctx.options.docker:
            self.step.services.append("docker")

    def is_parallel(self) -> bool:
        return bool(self.parallel_step_count)

    def should_install_docker_client(self) -> bool:
        if "docker" not in self.step.services:
            return False

        # PLR2004: Magic value used in comparison
        # SIM103: Return the negated condition directly
        if self.step.runtime_version >= 3:  # noqa: PLR2004, SIM103
            return False

        return True
