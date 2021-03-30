import base64
import getpass
import hashlib
import logging
import os
import posixpath
import re

from slugify import slugify


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

        self.username = getpass.getuser()

        self.build_container_base_memory_limit = 1024
        self.service_containers_base_memory_limit = 3072
        self.service_container_default_memory_limit = 1024

        # Randomly Generated
        # TODO: Generate them per project
        self.owner_uuid = "e07413cc-dcd9-4c68-aa2e-08e296b1a8af"
        self.repo_uuid = "8e6a16f2-c4cb-4973-a7c6-595626b29ceb"

        self.bitbucket_build_number = int(os.getenv("BITBUCKET_BUILD_NUMBER", 0))

        self.log_level = logging.getLevelName(os.getenv("PIPELINE_LOG_LEVEL", "DEBUG").upper())

    @property
    def project_directory(self):
        return self._project_directory

    @project_directory.setter
    def project_directory(self, value):
        self._project_directory = os.path.abspath(value)

    @property
    def pipeline_file(self):
        return os.path.join(self.project_directory, self._pipeline_file)

    @pipeline_file.setter
    def pipeline_file(self, value):
        self._pipeline_file = value

    @property
    def project_name(self):
        return os.path.basename(self.project_directory)

    @property
    def project_slug(self):
        return slugify(self.project_name)

    @property
    def project_env_name(self):
        h = hashlib.sha256(self.project_directory.encode()).digest()
        h = base64.urlsafe_b64encode(h).decode()[:8]

        return "{}-{}".format(self.project_slug, h)


config = Config()
