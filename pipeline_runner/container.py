import base64
import io
import logging
import os.path
import posixpath
import sys
import tarfile
import uuid
from collections.abc import Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from importlib.resources import as_file, files
from io import BufferedReader
from logging import Logger
from time import time
from typing import Any, TypedDict, cast

import boto3
import docker.errors  # type: ignore[import-untyped]
from docker import DockerClient
from docker.constants import DEFAULT_DATA_CHUNK_SIZE  # type: ignore[import-untyped]
from docker.models.containers import Container, ExecResult  # type: ignore[import-untyped]

import pipeline_runner
from pipeline_runner.context import StepRunContext

from .config import ATLASSIAN_DOCKER_CLI_VERSION, config
from .models import AwsCredentials, Image, Pipe
from .oidc import get_step_oidc_token
from .utils import escape_shell_string, stringify, wrap_in_shell

_group_separator = 0x1D
GROUP_SEPARATOR = chr(_group_separator)
ESCAPED_GROUP_SEPARATOR = f"\\x{_group_separator:02x}"

logger = logging.getLogger(__name__)

PipelineScript = Sequence[str | Pipe]


class ContainerRunner:
    def __init__(
        self,
        ctx: StepRunContext,
        name: str,
        image: Image,
        network_name: str | None,
        data_volume_name: str,
        env_vars: dict[str, str],
        output_logger: Logger,
        mem_limit: int = 512,
    ) -> None:
        self._ctx = ctx
        self._name = name
        self._image = image
        self._network_name = network_name
        self._repository_path = ctx.pipeline_ctx.repository.path
        self._data_volume_name = data_volume_name
        self._environment = env_vars
        self._logger = output_logger
        self._mem_limit = mem_limit * 2**20  # MiB to B
        self._ssh_private_key = ctx.pipeline_ctx.project_metadata.ssh_key
        self._platform = os.getenv("PIPELINE_RUNNER_DOCKER_PLATFORM")

        self._client = docker.from_env()
        self._container = None

    def start(self) -> None:
        self.start_container()
        self._create_pipeline_directories()
        self._insert_ssh_private_key()
        self._insert_ssh_known_hosts()

    def install_docker_client_if_needed(self, services: dict[str, Container]) -> None:
        if not self._container:
            # TODO: Refactor
            raise Exception("called on uninitialized container")

        if "docker" not in services:
            return

        res = self.run_command("command -v docker")
        if res.exit_code == 0:
            logger.debug("`docker` binary is already present in container.")
            return

        logger.debug("Injecting docker cli v%s in container", ATLASSIAN_DOCKER_CLI_VERSION)
        docker_cli_container = self._client.containers.run(
            f"docker:{ATLASSIAN_DOCKER_CLI_VERSION}-cli",
            name=f"{self._name}-docker-cli",
            entrypoint="sh",
            detach=True,
        )

        try:
            archive, _ = docker_cli_container.get_archive("/usr/local/bin/docker")
            self._container.put_archive("/usr/local/bin", archive)
        finally:
            docker_cli_container.remove(v=True, force=True)

    def stop(self) -> None:
        if not self._container:
            return

        logger.info("Removing container: %s", self._container.name)
        self._container.remove(v=True, force=True)

    def run_script(
        self,
        script: Sequence[str | Pipe],
        user: int | str | None = None,
        env: dict[str, Any] | None = None,
        *,
        exec_time: bool = False,
    ) -> int:
        csr = ContainerScriptRunnerFactory.get(self._container, script, self._logger, user, env, exec_time=exec_time)

        return csr.run()

    def run_command(self, command: str | list[str], user: int | str | None = None, *, shell: bool = True) -> ExecResult:
        if not self._container:
            # TODO: Refactor
            raise Exception("called on uninitialized container")

        command = stringify(command)

        if shell:
            command = wrap_in_shell(command)

        if user is not None:
            user = str(user)

        return self._container.exec_run(command, user=user)

    def path_exists(self, path: str) -> bool:
        ret, _ = self.run_command(f'[ -e "$(realpath "{path}")" ]')
        return cast("int", ret) == 0

    # TODO: Validate Typing
    def get_archive(
        self, path: str, chunk_size: int = DEFAULT_DATA_CHUNK_SIZE, *, encode_stream: bool = False
    ) -> tuple[Generator[bytes, None, None], dict[str, Any]]:
        if not self._container:
            # TODO: Refactor
            raise Exception("called on uninitialized container")

        return self._container.get_archive(path, chunk_size, encode_stream)

    # TODO: Validate Typing
    def put_archive(self, path: str, data: BufferedReader | bytes) -> bool:
        if not self._container:
            # TODO: Refactor
            raise Exception("called on uninitialized container")

        return self._container.put_archive(path, data)

    def start_container(self) -> None:
        pull_image(self._client, self._ctx, self._image, self._platform)

        logger.info("Creating container: %s", self._name)

        volumes = self._get_volumes()
        environment = self._environment.copy()

        opts = {"cpu_period": 100000, "cpu_quota": 400000, "cpu_shares": 4096} if config.cpu_limits else {}

        if config.expose_ssh_agent:
            ssh_agent_socket_path = get_ssh_agent_socket_path(self._client)

            if ssh_agent_socket_path:
                logger.info("Mounting ssh agent in container")
                volumes[ssh_agent_socket_path] = {"bind": "/ssh-agent"}
                environment["SSH_AUTH_SOCK"] = "/ssh-agent"
            else:
                logger.warning("No running ssh agent available")

        container = self._client.containers.run(
            self._image.name,
            name=self._name,
            entrypoint="sh",
            user=self._image.run_as_user or 0,
            working_dir=config.build_dir,
            environment=environment,
            volumes=volumes,
            mem_limit=self._mem_limit,
            network=self._network_name,
            tty=True,
            detach=True,
            platform=self._platform,
            **opts,
        )

        logger.debug("Created container: %s", container.name)
        logger.debug("Image Used: %s", self._image.name)

        self._container = container

    def get_container_name(self) -> str | None:
        if not self._container:
            return None

        return self._container.name

    def _create_pipeline_directories(self) -> None:
        mkdir_cmd = [
            "install",
            "-dD",
            "-o",
            str(self._image.run_as_user or 0),
            config.build_dir,
            config.scripts_dir,
            config.temp_dir,
            config.caches_dir,
            config.ssh_key_dir,
        ]

        exit_code, output = self.run_command(mkdir_cmd, user=0)
        if exit_code != 0:
            raise Exception(f"Error creating required directories: {output}")

    def _insert_ssh_private_key(self) -> None:
        static_files = files(pipeline_runner).joinpath("static")

        with as_file(static_files.joinpath("known_hosts")) as p:
            known_hosts = p.read_text()

        cmd = " && ".join(
            [
                "install -d -m 700 ~/.ssh",
                f"echo '{known_hosts}' >> ~/.ssh/known_hosts",
            ]
        )
        exit_code, output = self.run_command(cmd, user=0)
        if exit_code != 0:
            raise Exception(f"Error creating root ssh config: {output}")

    def _insert_ssh_known_hosts(self) -> None:
        if not self._ssh_private_key:
            return

        private_key_file_path = os.path.join(config.ssh_key_dir, "id_rsa")
        known_hosts_file_path = os.path.join(config.ssh_key_dir, "known_hosts")

        cmd = " && ".join(
            [
                "install -d -m 700 ~/.ssh",
                f'echo "IdentityFile {private_key_file_path}\nServerAliveInterval 180" > ~/.ssh/config',
                f"install -m 600 /dev/null {private_key_file_path}",
                f'echo "{self._ssh_private_key}" > {private_key_file_path}',
                # The default ssh key with open perms readable by alt uids
                f"install -m 644 {private_key_file_path} {private_key_file_path}_tmp",
                f"install -m 644 /dev/null {known_hosts_file_path}",
            ]
        )
        exit_code, output = self.run_command(cmd, user=0)
        if exit_code != 0:
            raise Exception(f"Error creating root ssh config: {output}")

    def _get_volumes(self) -> dict[str, dict[str, str]]:
        volumes = {}
        for volume in config.volumes:
            name, spec = self._parse_volume_spec(volume)
            volumes[name] = spec

        # Add the standard volumes at the end to ensure they can't be overwritten by the user
        volumes.update(
            {
                self._repository_path: {"bind": config.remote_workspace_dir, "mode": "ro"},
                self._data_volume_name: {"bind": config.remote_pipeline_dir},
            }
        )

        return volumes

    @staticmethod
    def _parse_volume_spec(volume: str) -> tuple[str, dict[str, str]]:
        parts = volume.split(":")
        name = parts[0]

        spec = {"bind": parts[1] if len(parts) > 1 else name}
        if len(parts) > 2:  # noqa: PLR2004  # Magic value used in comparison
            spec["mode"] = parts[2]

        return name, spec


@dataclass
class RemoteScript:
    entrypoint: str
    exit_code_file: str


@dataclass
class Breakpoint:
    pass


RunAction = RemoteScript | Breakpoint


class ContainerScriptRunner:
    def __init__(
        self,
        container: Container,
        script: PipelineScript,
        output_logger: Logger | None = None,
        user: int | str | None = None,
        env: dict[str, Any] | None = None,
    ) -> None:
        self._container = container
        self._script = script
        self._logger = output_logger
        self._user = str(user) if user is not None else None
        self._env = env or {}

        if logger_ := self._logger:

            def stdout_print(msg: str) -> None:
                logger_.info(msg)

            def stderr_print(msg: str) -> None:
                logger_.error(msg)

        else:

            def stdout_print(msg: str) -> None:
                print(msg, end="")  # noqa: T201  # `print` found

            def stderr_print(msg: str) -> None:
                print(msg, end="", file=sys.stderr)  # noqa: T201  # `print` found

        self._stdout_print = stdout_print
        self._stderr_print = stderr_print

    def run(self) -> int:
        run_actions = RemoteActionManager(self._script, self._container).get_actions()

        for act in run_actions:
            match act:
                case RemoteScript():
                    self._execute_script_on_container(act.entrypoint)
                    exit_code = self._get_exit_code_of_command(act.exit_code_file)

                    if exit_code != 0:
                        return exit_code
                case Breakpoint():
                    logger.info("Breakpoint")
                    logger.info(
                        "You can run a shell on the container with: docker exec -it %s sh", self._container.name
                    )
                    input("Press enter to continue")

        return 0

    def _execute_script_on_container(self, entrypoint: str) -> None:
        _, output_stream = self._container.exec_run(
            ["/bin/sh", entrypoint], user=self._user, tty=True, stream=True, demux=True, environment=self._env
        )

        self._print_execution_log(output_stream)

    def _print_execution_log(self, output_stream: Iterator[tuple[bytes, bytes]]) -> None:
        for stdout, stderr in output_stream:
            if stdout:
                self._stdout_print(stdout.decode().replace(GROUP_SEPARATOR, ""))
            if stderr:
                self._stderr_print(stderr.decode())

        self._stdout_print("\n")

    def _get_exit_code_of_command(self, exit_code_file_path: str) -> int:
        meta_exit_code, output = self._container.exec_run(["/bin/cat", exit_code_file_path])
        if meta_exit_code != 0:
            raise Exception(f"Error getting command exit code: {output.decode()}")

        str_code = output.decode().strip()

        try:
            exit_code = int(str_code)
        except ValueError:
            raise Exception(f"Invalid exit code: {str_code}") from None
        else:
            return exit_code


class ContainerScriptRunnerWithExecTime(ContainerScriptRunner):
    def __init__(
        self,
        container: Container,
        script: Sequence[str | Pipe],
        output_logger: Logger | None = None,
        user: int | str | None = None,
        env: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(container, script, output_logger, user, env)
        self._timestamp: float | None = None

    def _print_execution_log(self, output_stream: Iterator[tuple[bytes, bytes]]) -> None:
        for stdout, stderr in output_stream:
            if stdout:
                chunks = iter(stdout.decode().split(GROUP_SEPARATOR))

                self._stdout_print(next(chunks))

                for c in chunks:
                    self._print_timing()
                    self._stdout_print(c)
            if stderr:
                self._stderr_print(stderr.decode())

        self._print_timing()

    def _print_timing(self) -> None:
        now = time()
        if self._timestamp:
            self._stdout_print(f"\n>>> Execution time: {now - self._timestamp:.3f}s\n\n")

        self._timestamp = now


class ContainerScriptRunnerFactory:
    @staticmethod
    def get(
        container: Container,
        script: Sequence[str | Pipe],
        output_logger: Logger | None = None,
        user: int | str | None = None,
        env: dict[str, Any] | None = None,
        *,
        exec_time: bool = False,
    ) -> ContainerScriptRunner:
        cls: type[ContainerScriptRunner | ContainerScriptRunnerWithExecTime]

        cls = ContainerScriptRunnerWithExecTime if exec_time else ContainerScriptRunner

        return cls(container, script, output_logger, user, env)


class RemoteActionManager:
    def __init__(self, pipeline_script: PipelineScript, container: Container) -> None:
        self._pipeline_script = pipeline_script
        self._container = container

    def get_actions(self) -> list[RunAction]:
        entries = [e.as_cmd() if isinstance(e, Pipe) else e.strip() for e in self._pipeline_script]

        actions: list[RunAction] = []
        current: list[str] = []

        for e in entries:
            if e == "# pipeline-runner[breakpoint]":
                actions.append(self._prepare_for_remote_execution(current))
                actions.append(Breakpoint())
                current = []
            else:
                current.append(e)

        if current:
            actions.append(self._prepare_for_remote_execution(current))

        return actions

    def _prepare_for_remote_execution(self, script: list[str]) -> RemoteScript:
        traced_script = self._add_traces_to_script(script)
        exit_code_file_path = posixpath.join(config.temp_dir, f"exit_code-{uuid.uuid4().hex}")

        sh_script_name = f"shell_script-{uuid.uuid4().hex}.sh"
        sh_script_path = posixpath.join(config.scripts_dir, sh_script_name)
        sh_script = self._wrap_script_in_posix_shell(traced_script)

        bash_script_name = f"bash_script-{uuid.uuid4().hex}.sh"
        bash_script_path = posixpath.join(config.scripts_dir, bash_script_name)
        bash_script = self._wrap_script_in_bash(traced_script)

        wrapper_script_name = f"wrapper_script-{uuid.uuid4().hex}.sh"
        wrapper_script_path = posixpath.join(config.scripts_dir, wrapper_script_name)
        wrapper_script = self._make_wrapper_script(sh_script_path, bash_script_path, exit_code_file_path)

        scripts = (
            (sh_script_name, sh_script),
            (bash_script_name, bash_script),
            (wrapper_script_name, wrapper_script),
        )

        self._upload_to_container(scripts)
        return RemoteScript(entrypoint=wrapper_script_path, exit_code_file=exit_code_file_path)

    def _add_traces_to_script(self, script: list[str]) -> str:
        script_lines = map(self._add_trace_to_script_line, script)

        return '\nprintf "\\n"\n'.join(line for line in script_lines if line)

    def _add_trace_to_script_line(self, line: str | Pipe) -> str | None:
        line = line.as_cmd() if isinstance(line, Pipe) else line.strip()

        if not line:
            return None

        return f"{self._add_group_separator(line)}\n{line}"

    @staticmethod
    def _add_group_separator(value: str) -> str:
        value = escape_shell_string(value)

        return f'printf "{ESCAPED_GROUP_SEPARATOR}+ {value}\\n"'

    @staticmethod
    def _wrap_script_in_posix_shell(script: str) -> str:
        return f"#! /bin/sh\nset -e\n{script}"

    @staticmethod
    def _wrap_script_in_bash(script: str) -> str:
        return f"#! /bin/bash\nset -e\nset +H\n{script}"

    @staticmethod
    def _make_wrapper_script(sh_script_path: str, bash_script_path: str, exit_code_file_path: str) -> str:
        return "\n".join(
            [
                "#! /bin/sh",
                "if [ -f /bin/bash ]; then",
                f"    /bin/bash -i {bash_script_path}",
                f"    echo $? > {exit_code_file_path}",
                "    exit $?",
                "else",
                f"    /bin/sh {sh_script_path}",
                f"    echo $? > {exit_code_file_path}",
                "    exit $?",
                "fi",
            ]
        )

    def _upload_to_container(self, scripts: Iterable[tuple[str, str]]) -> None:
        tar_data = io.BytesIO()

        with tarfile.open(fileobj=tar_data, mode="w|") as tar:
            for name, script in scripts:
                ti = tarfile.TarInfo(name)
                script_data = script.encode()
                ti.size = len(script_data)
                ti.mode = 0o644

                tar.addfile(ti, io.BytesIO(script_data))

        res = self._container.put_archive(config.scripts_dir, tar_data.getvalue())
        if not res:
            raise Exception("Error uploading scripts to container")


_pulled_images = set()


def pull_image(client: DockerClient, step_ctx: StepRunContext, image: Image, platform: str | None = None) -> None:
    if image.name in _pulled_images:
        logger.info("Image already pulled: %s", image.name)
        return

    logger.info("Pulling image: %s", image.name)

    auth_config = get_image_authentication(step_ctx, image)
    try:
        client.images.pull(image.name, auth_config=auth_config, platform=platform)
    except docker.errors.NotFound:
        if client.images.get(image.name):
            logger.warning("Image not found on remote, but exists locally: %s", image.name)
        else:
            raise
    except docker.errors.APIError:
        if client.images.get(image.name):
            logger.warning("Error fetching new version of image, falling back to current one: %s", image.name)
        else:
            raise

    _pulled_images.add(image.name)


class DockerCredentials(TypedDict):
    username: str
    password: str


def get_image_authentication(ctx: StepRunContext, image: Image) -> DockerCredentials | None:
    if image.aws:
        return _get_aws_ecr_authentication(ctx, image.aws)

    if image.username and image.password:
        return DockerCredentials(username=image.username, password=image.password)

    return None


def _get_aws_ecr_authentication(ctx: StepRunContext, aws: AwsCredentials) -> DockerCredentials | None:
    aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    if config.oidc.enabled and aws.oidc_role:
        logger.info("Authenticating to AWS with OIDC")

        sts_client = boto3.client("sts", region_name=aws_region)
        oidc_token = get_step_oidc_token(ctx)

        response = sts_client.assume_role_with_web_identity(
            RoleArn=aws.oidc_role,
            RoleSessionName=f"pipeline-runner-step-{ctx.step_uuid}",
            WebIdentityToken=oidc_token,
            DurationSeconds=3600,
        )

        aws_access_key_id = response["Credentials"]["AccessKeyId"]
        aws_secret_access_key = response["Credentials"]["SecretAccessKey"]
        aws_session_token = response["Credentials"]["SessionToken"]
    else:
        # Try to authenticate with provided credentials, with fallback to the user's AWS creds
        aws_access_key_id = aws.access_key_id
        aws_secret_access_key = aws.secret_access_key
        aws_session_token = os.getenv("AWS_SESSION_TOKEN")

    client = boto3.client(
        "ecr",
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
        region_name=aws_region,
    )

    resp = client.get_authorization_token()

    credentials = base64.b64decode(resp["authorizationData"][0]["authorizationToken"]).decode()
    username, password = credentials.split(":", maxsplit=1)

    return DockerCredentials(username=username, password=password)


def get_ssh_agent_socket_path(client: DockerClient) -> str | None:
    ssh_sock_path: str | None

    if docker_is_docker_desktop(client):
        logger.debug("Using docker desktop's host service ssh agent")
        ssh_sock_path = "/run/host-services/ssh-auth.sock"
    elif ssh_sock_path := os.environ.get("SSH_AUTH_SOCK"):
        logger.debug("Using ssh agent specified by $SSH_AUTH_SOCK")
    else:
        return None

    return os.path.realpath(os.path.expanduser(ssh_sock_path))


def docker_is_docker_desktop(client: DockerClient) -> bool:
    platform_name: str

    try:
        platform_name = client.version()["Platform"]["Name"]
    except KeyError:
        platform_name = ""

    return platform_name.startswith("Docker Desktop ")
