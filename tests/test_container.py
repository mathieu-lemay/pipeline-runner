import base64

import pytest

from pipeline_runner.container import get_image_authentication
from pipeline_runner.models import AwsCredentials, Image


@pytest.fixture
def aws_lib(mocker):
    lib = mocker.patch("pipeline_runner.container.boto3")

    return lib


def test_get_image_authentication_returns_nothing_if_no_auth_defined():
    image = Image(name="alpine")

    assert get_image_authentication(image) is None


def test_get_image_authentication_returns_credentials_from_user_and_pass_if_they_are_specified():
    username = "some-username"
    password = "some-password"

    image = Image(name="alpine", username=username, password=password)

    assert get_image_authentication(image) == {
        "username": username,
        "password": password,
    }


def test_get_image_authentication_returns_credentials_from_aws_if_they_are_specified(aws_lib, mocker):
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


def test_aws_credentials_have_precedence(aws_lib):
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
