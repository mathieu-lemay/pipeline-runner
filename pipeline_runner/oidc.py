import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel, ConfigDict, Field

from pipeline_runner.config import config
from pipeline_runner.context import StepRunContext

if TYPE_CHECKING:
    import sys

    if sys.version_info < (3, 11):
        from typing_extensions import Self
    else:
        from typing import Self


logger = logging.getLogger(__name__)


class OIDCPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    iss: str
    aud: str
    sub: str
    iat: int
    exp: int

    account_uuid: str = Field(alias="accountUuid")
    workspace_uuid: str = Field(alias="workspaceUuid")
    repository_uuid: str = Field(alias="repositoryUuid")
    pipeline_uuid: str = Field(alias="pipelineUuid")
    step_uuid: str = Field(alias="stepUuid")
    deployment_environment_uuid: str | None = Field(alias="deploymentEnvironmentUuid", default=None)
    branch_name: str = Field(alias="branchName")

    @classmethod
    def new(cls, ctx: StepRunContext) -> "Self":
        oidc_settings = config.oidc

        now = datetime.now(tz=timezone.utc)
        iat = int(now.timestamp())
        exp = iat + 3600

        account_uuid = f"{{{ctx.pipeline_ctx.workspace_metadata.owner_uuid}}}"
        workspace_uuid = f"{{{ctx.pipeline_ctx.workspace_metadata.workspace_uuid}}}"
        repository_uuid = f"{{{ctx.pipeline_ctx.project_metadata.repo_uuid}}}"
        pipeline_uuid = f"{{{ctx.pipeline_ctx.pipeline_uuid}}}"
        step_uuid = f"{{{ctx.step_uuid}}}"
        branch_name = ctx.pipeline_ctx.repository.get_current_branch()

        if ctx.step.deployment:
            deployment_environment_uuid = f"{{{uuid.uuid5(uuid.NAMESPACE_OID, ctx.step.deployment)}}}"
            sub = f"{pipeline_uuid}:{deployment_environment_uuid}:{step_uuid}"
        else:
            deployment_environment_uuid = None
            sub = f"{pipeline_uuid}:{step_uuid}"

        return cls(
            iss=oidc_settings.issuer,
            aud=oidc_settings.audience,
            sub=sub,
            iat=iat,
            exp=exp,
            account_uuid=account_uuid,
            workspace_uuid=workspace_uuid,
            repository_uuid=repository_uuid,
            pipeline_uuid=pipeline_uuid,
            step_uuid=step_uuid,
            deployment_environment_uuid=deployment_environment_uuid,
            branch_name=branch_name,
        )


def get_step_oidc_token(ctx: StepRunContext) -> str:
    payload = OIDCPayload.new(ctx)

    public_key = load_pem_private_key(
        ctx.pipeline_ctx.workspace_metadata.oidc_private_key.encode(), password=None
    ).public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # TODO: py312: remove .decode()
    kid = uuid.uuid5(uuid.NAMESPACE_OID, public_key_pem.decode())

    return jwt.encode(
        payload.model_dump(),
        ctx.pipeline_ctx.workspace_metadata.oidc_private_key,
        algorithm="RS256",
        headers={"kid": str(kid)},
    )
