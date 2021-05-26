import json
import logging
import os
import uuid
from typing import Dict, List, Optional

from dotenv import dotenv_values
from slugify import slugify

from . import utils
from .config import config
from .models import CloneSettings, Image, Pipeline, PipelineInfo, Service, Step
from .parse import parse_pipeline_file
from .repository import Repository

logger = logging.getLogger(__name__)


class PipelineRunContext:
    def __init__(
        self,
        pipeline_name: str,
        pipeline: Pipeline,
        caches: Dict[str, str],
        services: Dict[str, Service],
        clone_settings: CloneSettings,
        default_image: Optional[Image],
        repository: Repository,
        env_vars: Optional[Dict[str, str]] = None,
        selected_steps: Optional[List[str]] = None,
    ):
        self.pipeline_name = pipeline_name
        self.pipeline = pipeline
        self.caches = self._merge_default_caches(caches)
        self.services = self._merge_default_services(services)
        self.clone_settings = clone_settings
        self.default_image = default_image
        self.repository = repository
        self.env_vars = env_vars or {}
        self.selected_steps = selected_steps or []

        self.pipeline_uuid = uuid.uuid4()
        self.build_number = self._get_build_number()
        self.pipeline_variables = {}

        self._data_directory = self._get_pipeline_data_directory()
        self._cache_directory = self._get_repo_cache_directory()

    @classmethod
    def from_run_request(cls, req) -> "PipelineRunContext":
        env_vars = cls._load_env_vars(req.env_files)
        spec = parse_pipeline_file(req.pipeline_file_path)
        spec.expand_env_vars(env_vars)

        pipeline_name = req.pipeline_name
        pipeline_to_run = spec.get_pipeline(pipeline_name)

        if not pipeline_to_run:
            msg = f"Invalid pipeline: {pipeline_name}"
            logger.error(msg)
            logger.info("Available pipelines:\n\t%s", "\n\t".join(sorted(spec.get_available_pipelines())))
            raise ValueError(msg)

        repository = Repository(req.repository_path)

        return PipelineRunContext(
            pipeline_name,
            pipeline_to_run,
            spec.caches,
            spec.services,
            spec.clone_settings,
            spec.image,
            repository,
            env_vars,
            req.selected_steps,
        )

    @staticmethod
    def _load_env_vars(env_files: List[str]) -> Dict[str, str]:
        envvars = {}
        # TODO: Load env file in the repo if exists
        logger.debug("Loading .env file (if exists)")
        envvars.update(dotenv_values(".env"))

        for env_file in env_files:
            if not os.path.exists(env_file):
                raise ValueError(f"Invalid env file: {env_file}")

            logger.debug("Loading env file: %s", env_file)
            envvars.update(dotenv_values(env_file))

        return envvars

    def _get_build_number(self):
        if config.bitbucket_build_number:
            return config.bitbucket_build_number

        pi = self.load_repository_metadata()
        pi.build_number += 1

        self.save_repository_metadata(pi)

        return pi.build_number

    @staticmethod
    def _merge_default_services(services: Dict[str, Service]) -> Dict[str, Service]:
        for name, definition in config.default_services.items():
            default_service = Service.parse_obj(definition)

            if name in services:
                service = services[name]
                service.image = default_service.image

                if not service.variables:
                    service.variables = default_service.variables
                if not service.memory:
                    service.memory = default_service.memory
            else:
                services[name] = default_service

        return services

    @staticmethod
    def _merge_default_caches(caches: Dict[str, str]) -> Dict[str, str]:
        all_caches = config.default_caches.copy()
        all_caches.update(caches)

        return all_caches

    def load_repository_metadata(self) -> PipelineInfo:
        fp = os.path.join(self._get_repo_data_directory(), "meta.json")

        if not os.path.exists(fp):
            return PipelineInfo()

        with open(fp) as f:
            return PipelineInfo.from_json(json.load(f))

    def save_repository_metadata(self, pi: PipelineInfo):
        repo_data_dir = utils.ensure_directory(self._get_repo_data_directory())

        fp = os.path.join(repo_data_dir, "meta.json")

        with open(fp, "w") as f:
            json.dump(pi.to_json(), f)

    def get_log_directory(self):
        return utils.ensure_directory(os.path.join(self._data_directory, "logs"))

    def get_artifact_directory(self):
        return utils.ensure_directory(os.path.join(self._data_directory, "artifacts"))

    def get_pipeline_cache_directory(self):
        return utils.ensure_directory(os.path.join(self._cache_directory, "caches"))

    def _get_repo_data_directory(self) -> str:
        return os.path.join(utils.get_data_directory(), self.repository.env_name)

    def _get_repo_cache_directory(self) -> str:
        return os.path.join(utils.get_cache_directory(), self.repository.env_name)

    def _get_pipeline_data_directory(self) -> str:
        return os.path.join(self._get_repo_data_directory(), "pipelines", f"{self.build_number}-{self.pipeline_uuid}")


class StepRunContext:
    def __init__(
        self,
        step: Step,
        pipeline_run_context: PipelineRunContext,
        parallel_step_index: Optional[int] = None,
        parallel_step_count: Optional[int] = None,
    ):
        self.step = step
        self.pipeline_ctx = pipeline_run_context
        self.slug = f"{pipeline_run_context.repository.slug}-step-{slugify(step.name)}"

        self.step_uuid = uuid.uuid4()

        if (parallel_step_index is None) != (parallel_step_count is None):
            raise ValueError("`parallel_step_index` and `parallel_step_count` must be both defined or both undefined")

        self.parallel_step_index = parallel_step_index
        self.parallel_step_count = parallel_step_count

    def is_parallel(self) -> bool:
        return bool(self.parallel_step_count)
