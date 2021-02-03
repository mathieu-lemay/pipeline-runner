import base64
import logging
import os.path
import sys
from typing import Dict, List, Optional, Union

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
    def __init__(self, image: Image, name: str, mem_limit: int, service_names: List[str]):
        self._image = image
        self._name = name
        self._mem_limit = mem_limit * 2 ** 20  # MiB to B
        self._service_names = service_names

        self._client = docker.from_env()
        self._container = None

    def start(self):
        pull_image(self._client, self._image)
        self._start_container()
        self._ensure_required_binaries()

        # TODO: Move to step setup
        self._clone_repository()

        return self._container

    def stop(self):
        if not self._container:
            return

        logger.info("Removing container: %s", self._container.name)
        self._container.remove(v=True, force=True)

    def run_script(self, script: Union[str, List[str]], shell: Optional[str] = "bash") -> int:
        command = utils.stringify(script, sep="\n")

        return self.run_command(command, shell)

    def run_command(
        self, command: Union[str, List[str]], shell: Optional[str] = "bash", user: Union[int, str] = 0
    ) -> int:
        command = utils.stringify(command)

        output_logger.info("+ " + command.replace("\n", "\n+ "))

        if shell:
            command = utils.wrap_in_shell(command, shell)

        exit_code, output = self._container.exec_run(command, user=str(user), tty=True)

        output_logger.info(output.decode())

        return exit_code

    def execute_in_container(
        self, command: Union[str, List[str]], shell: Optional[str] = "bash", user: Union[int, str] = 0
    ):
        command = utils.stringify(command)

        if shell:
            command = utils.wrap_in_shell(command, shell)

        return self._container.exec_run(command, user=str(user), tty=True)

    def get_archive(self, *args, **kwargs):
        return self._container.get_archive(*args, **kwargs)

    def put_archive(self, *args, **kwargs):
        return self._container.put_archive(*args, **kwargs)

    def expand_path(self, path) -> str:
        cmd = utils.wrap_in_shell(["echo", "-n", path])
        exit_code, output = self._container.exec_run(cmd, tty=True)
        if exit_code != 0:
            logger.error("Remote command failed: %s", output.decode())
            raise Exception(f"Error expanding path: {path}")

        return output.decode().strip()

    def _start_container(self):
        logger.info("Starting container")

        volumes = self._get_volumes()

        self._container = self._client.containers.run(
            self._image.name,
            name=self._name,
            entrypoint="sh",
            working_dir=config.build_dir,
            environment=self._get_env_vars(),
            volumes=volumes,
            mem_limit=self._mem_limit,
            links={s: s for s in self._service_names},
            tty=True,
            detach=True,
        )
        logger.debug("Created container: %s", self._container.name)

    def _clone_repository(self):
        # GIT_LFS_SKIP_SMUDGE=1 retry 6 git clone --branch="tbd/DRCT-455-enable-build-on-commits-to-trun"
        # --depth 50 https://x-token-auth:$REPOSITORY_OAUTH_ACCESS_TOKEN@bitbucket.org/$BITBUCKET_REPO_FULL_NAME.git
        # $BUILD_DIR

        exit_code = self.run_command(
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

        exit_code = self.run_command(["git", "reset", "--hard", "$BITBUCKET_COMMIT"])

        if exit_code:
            raise Exception("Error resetting to HEAD commit")

    def _get_env_vars(self):
        env_vars = self._get_pipelines_env_vars()

        env_vars.update(dotenv_values(".env"))
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
            "DOCKER_HOST": "tcp://docker:2375",
            "COMPOSE_DOCKER_CLI_BUILD": 0,
        }

    @staticmethod
    def _get_volumes():
        return {
            config.project_directory: {"bind": "/var/run/workspace", "mode": "ro"},
        }

    def _ensure_required_binaries(self):
        cmd = """
        if type apt-get >/dev/null 2>&1; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update && apt-get install -y --no-install-recommends bash docker.io git
        elif type apk >/dev/null 2>&1; then
            apk add --no-cache bash git docker-cli
        else
            echo "Unsupported distribution" >&2
            exit 1
        fi
        """

        if self.run_command(cmd, shell="sh", user=0) != 0:
            raise Exception("Error installing necessary binaries")


_pulled_images = set()


def pull_image(client, image):
    global _pulled_images

    if image.name in _pulled_images:
        logger.info("Image already pulled: %s", image.name)
        return

    logger.info("Pulling image: %s", image.name)

    auth_config = get_image_authentication(image)
    client.images.pull(image.name, auth_config=auth_config)

    _pulled_images.add(image.name)


def get_image_authentication(image: Image):
    if image.aws:
        aws_access_key_id = image.aws["access-key"]
        aws_secret_access_key = image.aws["secret-key"]
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

    if image.username and image.password:
        return {
            "username": image.username,
            "password": image.password,
        }

    return None
