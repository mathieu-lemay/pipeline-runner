import base64
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from pipeline_runner.config import Config
from pipeline_runner.container import ContainerRunner, get_image_authentication
from pipeline_runner.models import AwsCredentials, Image


@pytest.fixture()
def aws_lib(mocker: MockerFixture) -> MagicMock:
    lib = mocker.patch("pipeline_runner.container.boto3")

    return lib


@pytest.fixture()
def config(mocker: MockerFixture) -> Config:
    return mocker.patch("pipeline_runner.container.config")


def test_get_image_authentication_returns_nothing_if_no_auth_defined() -> None:
    image = Image(name="alpine")

    assert get_image_authentication(image) is None


def test_get_image_authentication_returns_credentials_from_user_and_pass_if_they_are_specified() -> None:
    username = "some-username"
    password = "some-password"

    image = Image(name="alpine", username=username, password=password)

    assert get_image_authentication(image) == {
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

    assert get_image_authentication(image) == {
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

    assert get_image_authentication(image) == {
        "username": aws_username,
        "password": aws_password,
    }


def test_cpu_limits_are_not_applied_if_config_is_set_to_false(config: Config, mocker: MockerFixture) -> None:
    runner = ContainerRunner(
        name="container",
        image=mocker.Mock(),
        network_name=None,
        repository_path="/some/path",
        data_volume_name="data-volume",
        env_vars={},
        output_logger=mocker.Mock(),
    )

    mocker.patch("pipeline_runner.container.pull_image")
    docker_client_mock = mocker.patch.object(runner, "_client")

    config.cpu_limits = False

    runner._start_container()

    assert docker_client_mock.containers.run.call_count == 1
    _, kwargs = docker_client_mock.containers.run.call_args

    assert "cpu_period" not in kwargs
    assert "cpu_quota" not in kwargs
    assert "cpu_shares" not in kwargs


def test_cpu_limits_are_applied_if_config_is_set_to_true(config: Config, mocker: MockerFixture) -> None:
    runner = ContainerRunner(
        name="container",
        image=mocker.Mock(),
        network_name=None,
        repository_path="/some/path",
        data_volume_name="data-volume",
        env_vars={},
        output_logger=mocker.Mock(),
    )

    mocker.patch("pipeline_runner.container.pull_image")
    docker_client_mock = mocker.patch.object(runner, "_client")

    config.cpu_limits = True

    runner._start_container()

    assert docker_client_mock.containers.run.call_count == 1
    _, kwargs = docker_client_mock.containers.run.call_args

    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 400_000
    assert kwargs["cpu_shares"] == 4096
