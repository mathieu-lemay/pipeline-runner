import json
import logging
import os
import uuid
from typing import Dict, Optional, Union

from dotenv import dotenv_values
from slugify import slugify

from . import utils
from .config import config
from .models import Cache, CloneSettings, Image, ParallelStep, Pipeline, PipelineInfo, Service, Step
from .parse import PipelinesFileParser
from .repository import Repository

logger = logging.getLogger(__name__)


class PipelineRunContext:
    def __init__(
        self,
        pipeline: Pipeline,
        caches: Dict[str, Cache],
        services: Dict[str, Service],
        clone_settings: CloneSettings,
        default_image: Optional[Image],
        repository: Repository,
        env_files: [str],
        selected_steps: [str],
    ):
        self.pipeline = pipeline
        self.caches = caches
        self.services = services
        self.clone_settings = clone_settings
        self.default_image = default_image
        self.repository = repository
        self.env_vars = self._load_env_vars(env_files)
        self.selected_steps = selected_steps

        self.pipeline_uuid = uuid.uuid4()
        self.build_number = self._get_build_number()

        self._data_directory = self._get_pipeline_data_directory()
        self._cache_directory = self._get_repo_cache_directory()

    @classmethod
    def from_run_request(cls, req) -> "PipelineRunContext":
        spec = PipelinesFileParser(req.pipeline_file_path).parse()

        pipeline_name = req.pipeline_name
        pipeline_to_run = spec.get_pipeline(pipeline_name)

        if not pipeline_to_run:
            msg = f"Invalid pipeline: {pipeline_name}"
            logger.error(msg)
            logger.info("Available pipelines:\n\t%s", "\n\t".join(sorted(spec.get_available_pipelines())))
            raise ValueError(msg)

        repository = Repository(req.repository_path)

        return PipelineRunContext(
            pipeline_to_run,
            spec.caches,
            spec.services,
            spec.clone_settings,
            spec.image,
            repository,
            req.env_files,
            req.selected_steps,
        )

    @staticmethod
    def _load_env_vars(env_files: [str]) -> Dict[str, str]:
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
        step: Union[Step, ParallelStep],
        pipeline_run_context: PipelineRunContext,
    ):
        self.step = step
        self.pipeline_ctx = pipeline_run_context
        self.slug = f"{pipeline_run_context.repository.slug}-step-{slugify(step.name)}"

        self.step_uuid = uuid.uuid4()

        # TODO:
        #   self.step_number = ...
        #   self.step_parallel_number = ...
