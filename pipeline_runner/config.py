import getpass
import logging
import os
import posixpath
from typing import Dict

from . import __name__ as __project_name__


class Config:
    def __init__(self):
        self.color = True
        self.cpu_limits = False

        # TODO: Move some of these things to default definitions or smth
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
                "image": "atlassian/pipelines-docker-daemon:v20-stable",
                "memory": 1024,
            }
        }

        self.remote_base_dir = "/opt/atlassian"
        self.remote_workspace_dir = os.path.join(self.remote_base_dir, "workspace")
        self.remote_pipeline_dir = os.path.join(self.remote_base_dir, "pipelines", "agent")
        self.build_dir = posixpath.join(self.remote_pipeline_dir, "build")
        self.scripts_dir = posixpath.join(self.remote_pipeline_dir, "scripts")
        self.temp_dir = posixpath.join(self.remote_pipeline_dir, "temp")
        self.caches_dir = posixpath.join(self.remote_pipeline_dir, "caches")
        self.ssh_key_dir = posixpath.join(self.remote_pipeline_dir, "ssh")

        self.username = getpass.getuser()

        self.total_memory_limit = 4096
        self.build_container_minimum_memory = 1024
        self.service_container_default_memory_limit = 1024

        # Randomly Generated
        # TODO: Generate them per project
        self.owner_uuid = "e07413cc-dcd9-4c68-aa2e-08e296b1a8af"

        self.bitbucket_build_number = os.getenv("BITBUCKET_BUILD_NUMBER", 0)

        self.log_level = logging.getLevelName(os.getenv("PIPELINE_LOG_LEVEL", "DEBUG").upper())

    @property
    def log_config(self) -> Dict:
        log_handler_name = "colored" if self.color and "NO_COLOR" not in os.environ else "default"
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
