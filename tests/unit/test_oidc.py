import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from faker.proxy import Faker
from pytest_mock import MockerFixture

from pipeline_runner.config import Config
from pipeline_runner.oidc import OIDCPayload, get_step_oidc_token
from pipeline_runner.utils import generate_rsa_key


@pytest.mark.parametrize("deployment_environment", ([None, "some-env"]))
def test_oidc_payload_new(
    mocker: MockerFixture, config: Config, faker: Faker, deployment_environment: str | None
) -> None:
    timestamp = datetime.now(tz=timezone.utc)
    mock_datetime = Mock()
    mock_datetime.now.return_value = timestamp

    mocker.patch("pipeline_runner.oidc.datetime", mock_datetime)

    issuer = f"https://oidc.{faker.safe_domain_name()}"
    audience = faker.pystr()

    config.oidc.issuer = issuer
    config.oidc.audience = audience

    account_uuid = uuid.uuid4()
    workspace_uuid = uuid.uuid4()
    repository_uuid = uuid.uuid4()
    pipeline_uuid = uuid.uuid4()
    step_uuid = uuid.uuid4()
    branch_name = faker.pystr()

    step_ctx = Mock()
    step_ctx.step_uuid = step_uuid
    step_ctx.pipeline_ctx.workspace_metadata.owner_uuid = account_uuid
    step_ctx.pipeline_ctx.workspace_metadata.workspace_uuid = workspace_uuid
    step_ctx.pipeline_ctx.project_metadata.repo_uuid = repository_uuid
    step_ctx.pipeline_ctx.pipeline_uuid = pipeline_uuid
    step_ctx.pipeline_ctx.repository.get_current_branch.return_value = branch_name
    step_ctx.step.deployment = deployment_environment

    payload = OIDCPayload.new(step_ctx)

    iat = int(timestamp.timestamp())
    exp = iat + 3600

    if deployment_environment:
        deployment_environment_uuid = uuid.uuid5(uuid.NAMESPACE_OID, deployment_environment)
        subject = f"{{{pipeline_uuid}}}:{{{deployment_environment_uuid}}}:{{{step_uuid}}}"
    else:
        deployment_environment_uuid = None
        subject = f"{{{pipeline_uuid}}}:{{{step_uuid}}}"

    expected_payload = OIDCPayload(
        iss=issuer,
        aud=audience,
        sub=subject,
        iat=iat,
        exp=exp,
        account_uuid=f"{{{account_uuid}}}",
        workspace_uuid=f"{{{workspace_uuid}}}",
        repository_uuid=f"{{{repository_uuid}}}",
        pipeline_uuid=f"{{{pipeline_uuid}}}",
        step_uuid=f"{{{step_uuid}}}",
        branch_name=branch_name,
    )
    if deployment_environment_uuid:
        expected_payload.deployment_environment_uuid = f"{{{deployment_environment_uuid}}}"

    assert payload == expected_payload


def test_get_oidc_token_returns_a_valid_token(mocker: MockerFixture, faker: Faker) -> None:
    issuer = f"https://oidc.{faker.safe_domain_name()}"
    audience = faker.pystr()

    now = int(datetime.now(tz=timezone.utc).timestamp())

    payload = OIDCPayload(
        iss=issuer,
        aud=audience,
        sub=faker.pystr(),
        iat=now,
        exp=now + faker.pyint(min_value=0, max_value=7200),
        account_uuid=faker.pystr(),
        workspace_uuid=faker.pystr(),
        repository_uuid=faker.pystr(),
        pipeline_uuid=faker.pystr(),
        step_uuid=faker.pystr(),
        branch_name=faker.pystr(),
    )
    mocker.patch.object(OIDCPayload, "new", return_value=payload)

    private_key = generate_rsa_key()
    public_key = load_pem_private_key(private_key.encode(), password=None).public_key()

    step_ctx = Mock()
    step_ctx.pipeline_ctx.workspace_metadata.oidc_private_key = private_key

    token = get_step_oidc_token(step_ctx)

    assert isinstance(public_key, RSAPublicKey)  # type check for jwt.decode
    decoded_token = jwt.decode(token, public_key, algorithms=["RS256"], audience=audience)
    assert decoded_token == payload.model_dump()

    headers = jwt.get_unverified_header(token)
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # TODO: py312: remove .decode()
    expected_kid = uuid.uuid5(uuid.NAMESPACE_OID, public_key_pem.decode())

    assert headers == {
        "alg": "RS256",
        "typ": "JWT",
        "kid": str(expected_kid),
    }
