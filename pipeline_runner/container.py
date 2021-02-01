import base64
import logging
import os.path
import sys
from typing import Dict, List, Union

import boto3
import docker
from dotenv import dotenv_values

from . import utils
from .config import config
from .models import Image

logger = logging.getLogger(__name__)

handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(logging.Formatter("%(message)s"))
output_logger = logging.getLogger("output")
output_logger.handlers.append(handler)
output_logger.setLevel("INFO")


class ContainerRunner:
    def __init__(self, image: Image, name: str):
        self._image = image
        self._name = name

        self._client = docker.from_env()
        self._container = None

    def start(self):
        self._pull_image()
        self._start_container()

        # TODO: Move to step setup
        # self._start_services()
        self._clone_repository()

        return self._container

    def stop(self):
        if not self._container:
            return

        logger.info("Removing container: %s", self._container.name)
        self._container.remove(v=True, force=True)

    def run_script(self, script: Union[str, List[str]], shell=True) -> int:
        command = utils.stringify(script, sep="\n")

        return self.exec(command, shell)

    def exec(self, command: Union[str, List[str]], shell=True) -> int:
        try:
            exit_code = self._execute(command, shell)
        except Exception as e:
            logger.exception(f"Error running script in docker container: {e}")
            raise
        else:
            return exit_code

    def get_archive(self, *args, **kwargs):
        return self._container.get_archive(*args, **kwargs)

    def put_archive(self, *args, **kwargs):
        return self._container.put_archive(*args, **kwargs)

    def _execute(self, command: Union[str, List[str]], shell):
        command = utils.stringify(command)

        output_logger.info("+ " + command.replace("\n", "\n+ "))

        if shell:
            command = self._wrap_in_shell(command)

        exit_code, output = self._container.exec_run(command, tty=True)
        logger.debug("Command exited with code: %d", exit_code)

        output_logger.info(output.decode())

        return exit_code

    @staticmethod
    def _wrap_in_shell(cmd):
        return ["sh", "-e", "-c", cmd]

    def _pull_image(self):
        logger.info("Pulling image: %s", self._image.name)

        auth_config = self._get_docker_auth_config()
        self._client.images.pull(self._image.name, auth_config=auth_config)

    def _start_container(self):
        logger.info("Starting container")

        volumes = self._get_volumes()

        self._container = self._client.containers.run(
            self._image.name,
            name=self._name,
            entrypoint="sh",
            tty=True,
            detach=True,
            remove=True,
            working_dir=config.build_dir,
            environment=self._get_env_vars(),
            volumes=volumes,
        )
        logger.debug("Created container: %s", self._container.name)

    def _clone_repository(self):
        # GIT_LFS_SKIP_SMUDGE=1 retry 6 git clone --branch="tbd/DRCT-455-enable-build-on-commits-to-trun"
        # --depth 50 https://x-token-auth:$REPOSITORY_OAUTH_ACCESS_TOKEN@bitbucket.org/$BITBUCKET_REPO_FULL_NAME.git
        # $BUILD_DIR

        exit_code = self.exec(
            [
                "GIT_LFS_SKIP_SMUDGE=1",
                "git",
                "clone",
                "--branch",
                utils.get_git_current_branch(),
                "--depth",
                "50",
                "/var/run/workspace",
                "$BUILD_DIR",
            ]
        )

        if exit_code:
            raise Exception("Error cloning repository")

        exit_code = self.exec(["git", "reset", "--hard", "$BITBUCKET_COMMIT"])

        if exit_code:
            raise Exception("Error resetting to HEAD commit")

    def _get_docker_auth_config(self):
        if self._image.aws:
            aws_access_key_id = self._image.aws["access-key"]
            aws_secret_access_key = self._image.aws["secret-key"]
            aws_session_token = os.getenv("AWS_SESSION_TOKEN")

            client = boto3.client(
                "ecr",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name="ca-central-1",
            )

            resp = client.get_authorization_token()

            credentials = base64.b64decode(resp["authorizationData"][0]["authorizationToken"]).decode()
            username, password = credentials.split(":", maxsplit=1)

            return {
                "username": username,
                "password": password,
            }

        if self._image.username and self._image.password:
            return {
                "username": self._image.username,
                "password": self._image.password,
            }

        return None

    def _get_env_vars(self):
        env_vars = self._get_pipelines_env_vars()

        env_vars.update(dotenv_values())
        for env_file in config.env_files:
            env_vars.update(dotenv_values(env_file))

        return env_vars

    @staticmethod
    def _get_pipelines_env_vars() -> Dict[str, str]:
        project_slug = config.project_slug
        git_branch = utils.get_git_current_branch()
        git_commit = utils.get_git_current_commit()

        return {
            "BUILD_DIR": config.build_dir,
            "BITBUCKET_BRANCH": git_branch,
            "BITBUCKET_BUILD_NUMBER": 1,
            "BITBUCKET_CLONE_DIR": config.build_dir,
            "BITBUCKET_COMMIT": git_commit,
            "BITBUCKET_EXIT_CODE": 0,
            "BITBUCKET_PROJECT_KEY": "PR",
            "BITBUCKET_REPO_FULL_NAME": f"{project_slug}/{project_slug}",
            "BITBUCKET_REPO_IS_PRIVATE": "true",
            "BITBUCKET_REPO_OWNER": config.username,
            "BITBUCKET_REPO_OWNER_UUID": config.owner_uuid,
            "BITBUCKET_REPO_SLUG": project_slug,
            "BITBUCKET_REPO_UUID": config.repo_uuid,
            "BITBUCKET_WORKSPACE": project_slug,
            "COMPOSE_DOCKER_CLI_BUILD": 0,
        }

    @staticmethod
    def _get_volumes():
        return {
            config.project_directory: {"bind": "/var/run/workspace", "mode": "ro"},
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock"},
        }

    # def _start_services(self):
    #     if "docker" in self._step.services:
    #         bin_path = self._ensure_docker_binary()

    # @staticmethod
    # def _ensure_docker_binary():
    #     data_dir = get_data_directory()
    #     docker_binary_path = os.path.join(data_dir, "docker", "docker")
    #     if os.path.exists(docker_binary_path):
    #         return docker_binary_path
    #
    #     resp = requests.get("https://download.docker.com/linux/static/stable/x86_64/docker-20.10.2.tgz", stream=True)
    #     # noinspection PyTypeChecker
    #     with tarfile.open(fileobj=FileStreamer(resp.iter_content(chunk_size=1024 * 1024)), mode="r|gz") as f:
    #         f.extractall(data_dir)
    #
    #     return docker_binary_path
