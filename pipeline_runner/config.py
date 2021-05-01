import base64
import getpass
import hashlib
import logging
import os
import posixpath
import re
from typing import Dict

from slugify import slugify

from . import __name__ as __project_name__


class Config:
    def __init__(self):
        self._project_directory = None
        self._pipeline_file = None

        self.project_directory = os.getenv("PIPELINE_PROJECT_DIRECTORY", os.getcwd())
        self.pipeline_file = os.getenv("PIPELINE_FILE", "bitbucket-pipelines.yml")
        self.env_files = (
            re.split("[:;,]", os.environ["PIPELINE_ENV_FILES"]) if "PIPELINE_ENV_FILES" in os.environ else []
        )
        self.selected_steps = re.split("[:;,]", os.environ["PIPELINE_STEPS"]) if "PIPELINE_STEPS" in os.environ else []
        self.color = True

        self.default_image = "atlassian/default-image:latest"

        self.default_caches = {
            "composer": "~/.composer/cache",
            "dotnetcore": "~/.nuget/packages",
            "gradle": "~/.gradle/caches ",
            "ivy2": "~/.ivy2/cache",
            "maven": "~/.m2/repository",
            "node": "node_modules",
            "pip": "~/.cache/pip",
            "sbt": "~/.sbt",
        }

        self.default_services = {
            "docker": {
                "image": "docker:dind",
                "memory": 1024,
                "command": "--tls=false",
                "environment": {"DOCKER_TLS_CERTDIR": None},
            }
        }

        self.remote_base_dir = "/opt/pipeline-runner"
        self.remote_workspace_dir = os.path.join(self.remote_base_dir, "workspace")
        self.remote_pipeline_dir = os.path.join(self.remote_base_dir, "pipeline")
        self.build_dir = posixpath.join(self.remote_pipeline_dir, "build")
        self.scripts_dir = posixpath.join(self.remote_pipeline_dir, "scripts")
        self.temp_dir = posixpath.join(self.remote_pipeline_dir, "temp")
        self.caches_dir = posixpath.join(self.remote_pipeline_dir, "caches")

        self.username = getpass.getuser()

        self.total_memory_limit = 4096
        self.build_container_minimum_memory = 1024
        self.service_container_default_memory_limit = 1024

        # Randomly Generated
        # TODO: Generate them per project
        self.owner_uuid = "e07413cc-dcd9-4c68-aa2e-08e296b1a8af"
        self.repo_uuid = "8e6a16f2-c4cb-4973-a7c6-595626b29ceb"

        self.bitbucket_build_number = int(os.getenv("BITBUCKET_BUILD_NUMBER", 0))

        self.log_level = logging.getLevelName(os.getenv("PIPELINE_LOG_LEVEL", "DEBUG").upper())

    @property
    def project_directory(self) -> str:
        return self._project_directory

    @project_directory.setter
    def project_directory(self, value: str):
        self._project_directory = os.path.abspath(value)

    @property
    def pipeline_file(self) -> str:
        return os.path.join(self.project_directory, self._pipeline_file)

    @pipeline_file.setter
    def pipeline_file(self, value: str):
        self._pipeline_file = value

    @property
    def project_name(self) -> str:
        return os.path.basename(self.project_directory)

    @property
    def project_slug(self) -> str:
        return slugify(self.project_name)

    @property
    def project_env_name(self) -> str:
        h = hashlib.sha256(self.project_directory.encode()).digest()
        h = base64.urlsafe_b64encode(h).decode()[:8]

        return "{}-{}".format(self.project_slug, h)

    @property
    def log_config(self) -> Dict:
        log_handler_name = "colored" if self.color else "default"
        return {
            "version": 1,
            "loggers": {
                __project_name__: {"handlers": [log_handler_name], "level": config.log_level},
                "docker": {"handlers": ["default"], "level": "INFO"},
            },
            "handlers": {
                "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
                "colored": {"formatter": "colored", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
            },
            "formatters": {
                "default": {
                    "format": "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "colored": {
                    "()": "coloredlogs.ColoredFormatter",
                    "format": "%(asctime)s.%(msecs)03d %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
        }


config = Config()
