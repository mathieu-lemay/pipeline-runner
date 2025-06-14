import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from faker.proxy import Faker
from pytest_mock import MockerFixture

from pipeline_runner.config import config
from pipeline_runner.oidc import OIDCPayload


@pytest.mark.parametrize("deployment_environment", ([None, "some-env"]))
def test_oidc_payload_new(mocker: MockerFixture, faker: Faker, deployment_environment: str | None) -> None:
    timestamp = datetime.now(tz=UTC)
    mock_datetime = Mock()
    mock_datetime.now.return_value = timestamp

    mocker.patch("pipeline_runner.oidc.datetime", mock_datetime)

    issuer = f"https://oidc.{faker.safe_domain_name()}"
    audience = faker.pystr()

    mocker.patch.object(config.oidc, "issuer", new=issuer)
    mocker.patch.object(config.oidc, "audience", new=audience)

    account_uuid = uuid.uuid4()
    workspace_uuid = uuid.uuid4()
    repository_uuid = uuid.uuid4()
    pipeline_uuid = uuid.uuid4()
    step_uuid = uuid.uuid4()
    branch_name = faker.pystr()

    step_ctx = Mock()
    step_ctx.step_uuid = step_uuid
    step_ctx.pipeline_ctx.project_metadata.owner_uuid = account_uuid
    step_ctx.pipeline_ctx.project_metadata.project_uuid = workspace_uuid
    step_ctx.pipeline_ctx.project_metadata.repo_uuid = repository_uuid
    step_ctx.pipeline_ctx.pipeline_uuid = pipeline_uuid
    step_ctx.pipeline_ctx.repository.get_current_branch.return_value = branch_name

    payload = OIDCPayload.new(step_ctx, deployment_environment)

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
