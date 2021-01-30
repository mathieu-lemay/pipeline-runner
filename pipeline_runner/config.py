import getpass
import os
import re

from slugify import slugify


class Config:
    def __init__(self):
        self.project_directory = os.getenv("PIPELINE_PROJECT_DIRECTORY", os.getcwd())
        self.pipeline_file = os.getenv("PIPELINE_FILE", "bitbucket-pipelines.yml")
        self.env_files = (
            re.split("[:;,]", os.environ["PIPELINE_ENV_FILES"]) if "PIPELINE_ENV_FILES" in os.environ else []
        )
        self.selected_steps = re.split("[:;,]", os.environ["PIPELINE_STEPS"]) if "PIPELINE_STEPS" in os.environ else []

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

        self.build_dir = "/var/run/pipeline"
        self.username = getpass.getuser()

        # Randomly Generated
        # TODO: Generate them per project
        self.owner_uuid = "e07413cc-dcd9-4c68-aa2e-08e296b1a8af"
        self.repo_uuid = "8e6a16f2-c4cb-4973-a7c6-595626b29ceb"

    @property
    def project_name(self):
        return os.path.basename(self.project_directory)

    @property
    def project_slug(self):
        return slugify(self.project_name)
