import base64
import os
import platform
from collections.abc import Callable
from unittest.mock import ANY, MagicMock, Mock, call

import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from docker import DockerClient  # type: ignore[import-untyped]
from faker import Faker
from pytest_mock import MockerFixture

from pipeline_runner.config import Config
from pipeline_runner.container import (
    ContainerRunner,
    docker_is_docker_desktop,
    get_image_authentication,
    get_ssh_agent_socket_path,
    is_running_on_windows,
)
from pipeline_runner.context import StepRunContext
from pipeline_runner.models import AwsCredentials, Image


@pytest.fixture
def aws_lib(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("pipeline_runner.container.boto3")


@pytest.fixture
def docker_is_docker_desktop_mock(mocker: MockerFixture) -> Callable[[DockerClient], bool]:
    return mocker.patch("pipeline_runner.container.docker_is_docker_desktop")


def test_get_image_authentication_returns_nothing_if_no_auth_defined() -> None:
    image = Image(name="alpine")

    assert get_image_authentication(Mock(), image) is None


def test_get_image_authentication_returns_credentials_from_user_and_pass_if_they_are_specified() -> None:
    username = "some-username"
    password = "some-password"

    image = Image(name="alpine", username=username, password=password)

    assert get_image_authentication(Mock(), image) == {
        "username": username,
        "password": password,
    }


def test_get_image_authentication_returns_credentials_from_aws_if_they_are_specified(
    aws_lib: MagicMock, mocker: MockerFixture
) -> None:
    access_key_id = "my-access-key-id"
    secret_access_key = "my-secret-access-key"
    session_token = "my-session-token"
    region = "us-west-2"

    username = "the-aws-username"
    password = "the-aws-password"
    auth_token = base64.b64encode(f"{username}:{password}".encode()).decode()

    environ = {
        "AWS_SESSION_TOKEN": session_token,
        "AWS_DEFAULT_REGION": region,
    }
    mocker.patch.dict("os.environ", environ)

    creds = AwsCredentials(access_key_id=access_key_id, secret_access_key=secret_access_key)
    image = Image(name="alpine", aws=creds)

    client = aws_lib.client.return_value
    client.get_authorization_token.return_value = {"authorizationData": [{"authorizationToken": auth_token}]}

    assert get_image_authentication(Mock(), image) == {
        "username": username,
        "password": password,
    }


def test_aws_credentials_have_precedence(aws_lib: MagicMock) -> None:
    access_key_id = "my-access-key-id"
    secret_access_key = "my-secret-access-key"

    aws_username = "the-aws-username"
    aws_password = "the-aws-password"
    auth_token = base64.b64encode(f"{aws_username}:{aws_password}".encode()).decode()

    username = "plain-username"
    password = "plain-password"

    creds = AwsCredentials(access_key_id=access_key_id, secret_access_key=secret_access_key)
    image = Image(name="alpine", username=username, password=password, aws=creds)

    client = aws_lib.client.return_value
    client.get_authorization_token.return_value = {"authorizationData": [{"authorizationToken": auth_token}]}

    assert get_image_authentication(Mock(), image) == {
        "username": aws_username,
        "password": aws_password,
    }


def test_aws_credentials_oidc(aws_lib: MagicMock, config: Config, mocker: MockerFixture, faker: Faker) -> None:
    config.oidc.enabled = True

    oidc_role = faker.pystr()

    creds = AwsCredentials(oidc_role=oidc_role)
    image = Image(name="alpine", aws=creds)

    ctx = Mock(spec=StepRunContext)
    ctx.step_uuid = faker.uuid4()

    oidc_token = faker.pystr()

    aws_access_key_id = faker.pystr()
    aws_secret_access_key = faker.pystr()
    aws_session_token = faker.pystr()

    aws_username = faker.pystr()
    aws_password = faker.pystr()
    auth_token = base64.b64encode(f"{aws_username}:{aws_password}".encode()).decode()

    get_step_oidc_token = mocker.patch("pipeline_runner.container.get_step_oidc_token")
    get_step_oidc_token.return_value = oidc_token

    client = aws_lib.client.return_value
    client.assume_role_with_web_identity.return_value = {
        "Credentials": {
            "AccessKeyId": aws_access_key_id,
            "SecretAccessKey": aws_secret_access_key,
            "SessionToken": aws_session_token,
        }
    }
    client.get_authorization_token.return_value = {"authorizationData": [{"authorizationToken": auth_token}]}

    assert get_image_authentication(ctx, image) == {
        "username": aws_username,
        "password": aws_password,
    }

    get_step_oidc_token.assert_called_with(ctx)

    aws_lib.client.assert_has_calls(
        (
            call("sts", region_name=ANY),
            call(
                "ecr",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=ANY,
            ),
        ),
        any_order=True,
    )
    client.assume_role_with_web_identity.assert_called_once_with(
        RoleArn=oidc_role,
        RoleSessionName=f"pipeline-runner-step-{ctx.step_uuid}",
        WebIdentityToken=oidc_token,
        DurationSeconds=3600,
    )
    client.get_authorization_token.assert_called_once_with()


def test_cpu_limits_are_not_applied_if_config_is_set_to_false(
    config: Config, mocker: MockerFixture, faker: Faker
) -> None:
    step = Mock()
    step.name = faker.pystr()

    ctx = StepRunContext(step=step, pipeline_ctx=Mock())

    runner = ContainerRunner(
        ctx=ctx,
        name="container",
        image=mocker.Mock(),
        network_name=None,
        data_volume_name="data-volume",
        env_vars={},
        output_logger=mocker.Mock(),
    )

    mocker.patch("pipeline_runner.container.pull_image")
    docker_client_mock = mocker.patch.object(runner, "_client")

    config.cpu_limits = False

    runner.start_container()

    assert docker_client_mock.containers.run.call_count == 1
    _, kwargs = docker_client_mock.containers.run.call_args

    assert "cpu_period" not in kwargs
    assert "cpu_quota" not in kwargs
    assert "cpu_shares" not in kwargs


def test_cpu_limits_are_applied_if_config_is_set_to_true(config: Config, mocker: MockerFixture, faker: Faker) -> None:
    step = Mock()
    step.name = faker.pystr()

    ctx = StepRunContext(step=step, pipeline_ctx=Mock())

    runner = ContainerRunner(
        ctx=ctx,
        name="container",
        image=mocker.Mock(),
        network_name=None,
        data_volume_name="data-volume",
        env_vars={},
        output_logger=mocker.Mock(),
    )

    mocker.patch("pipeline_runner.container.pull_image")
    docker_client_mock = mocker.patch.object(runner, "_client")

    config.cpu_limits = True

    runner.start_container()

    assert docker_client_mock.containers.run.call_count == 1
    _, kwargs = docker_client_mock.containers.run.call_args

    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 400_000
    assert kwargs["cpu_shares"] == 4096


@pytest.mark.parametrize(
    ("system", "release", "is_windows"),
    [
        ("Windows", "3.11 for workspace", True),
        ("Linux", "6.6.87.2-microsoft-standard-WSL2", True),
        ("Linux", "4.20", False),
    ],
)
def test_is_running_on_windows(
    faker: Faker,
    mocker: MockerFixture,
    system: str,
    release: str,
    is_windows: bool,
) -> None:
    uname = platform.uname_result(system, faker.pystr(), release, faker.pystr(), faker.pystr())
    mocker.patch("pipeline_runner.container.platform.uname", return_value=uname)

    assert is_running_on_windows() == is_windows


def test_get_ssh_agent_socket_path_returns_nothing_if_none_is_found(
    monkeypatch: MonkeyPatch,
    docker_is_docker_desktop_mock: MagicMock,
) -> None:
    client = MagicMock(DockerClient)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    docker_is_docker_desktop_mock.return_value = False

    assert get_ssh_agent_socket_path(client) is None


def test_get_ssh_agent_socket_path_returns_docker_desktops_host_service_agent(
    monkeypatch: MonkeyPatch,
    docker_is_docker_desktop_mock: MagicMock,
    caplog: LogCaptureFixture,
) -> None:
    client = MagicMock(DockerClient)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    docker_is_docker_desktop_mock.return_value = True

    assert get_ssh_agent_socket_path(client) == "/run/host-services/ssh-auth.sock"
    assert "Using docker desktop's host service ssh agent" in caplog.text


def test_get_ssh_agent_socket_path_returns_none_for_docker_desktop_for_windows(
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
    docker_is_docker_desktop_mock: MagicMock,
    caplog: LogCaptureFixture,
) -> None:
    client = MagicMock(DockerClient)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    docker_is_docker_desktop_mock.return_value = True

    mocker.patch("pipeline_runner.container.is_running_on_windows", return_value=True)

    assert get_ssh_agent_socket_path(client) is None
    assert "ssh agent forwarding is not supported on Docker Desktop for Windows" in caplog.text


def test_get_ssh_agent_socket_path_returns_value_of_ssh_auth_sock_env(
    monkeypatch: MonkeyPatch,
    docker_is_docker_desktop_mock: MagicMock,
    caplog: LogCaptureFixture,
) -> None:
    value = "/some/path/to/ssh/socket"
    client = MagicMock(DockerClient)
    monkeypatch.setenv("SSH_AUTH_SOCK", value)
    docker_is_docker_desktop_mock.return_value = False

    assert get_ssh_agent_socket_path(client) == value
    assert "Using ssh agent specified by $SSH_AUTH_SOCK" in caplog.text


def test_get_ssh_agent_socket_path_returns_expanded_real_path(
    monkeypatch: MonkeyPatch,
    docker_is_docker_desktop_mock: MagicMock,
) -> None:
    value = "~/some/path/to/symlink"
    client = MagicMock(DockerClient)
    monkeypatch.setenv("SSH_AUTH_SOCK", value)
    docker_is_docker_desktop_mock.return_value = False

    def expanduser(val: str) -> str:
        return f"{val}+expanded"

    def realpath(val: str) -> str:
        return f"{val}+realpath"

    monkeypatch.setattr(os.path, "expanduser", expanduser)
    monkeypatch.setattr(os.path, "realpath", realpath)

    assert get_ssh_agent_socket_path(client) == f"{value}+expanded+realpath"


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [
        (None, False),
        ("", False),
        ("Docker Engine - Community", False),
        ("Docker DesktopButNotReally", False),
        ("Docker Desktop 4.29.0 (145265)", True),
    ],
)
def test_docker_is_docker_desktop(platform_name: str | None, expected: bool) -> None:
    client = MagicMock(DockerClient)

    if platform_name is not None:
        client.version.return_value = {"Platform": {"Name": platform_name}}
    else:
        # It seems that the Platform and Name keys are always present, but make sure we handle it if they are not.
        client.version.return_value = {"Platform": {}}

    assert docker_is_docker_desktop(client) == expected
