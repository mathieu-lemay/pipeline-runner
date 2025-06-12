import logging
import uuid
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid5

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from pydantic import BaseModel, ConfigDict, Field

from pipeline_runner.models import ProjectMetadata

if TYPE_CHECKING:
    from typing import Self

logger = logging.getLogger(__name__)


class OidcPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    iss: str
    sub: str
    aud: str = "rogueconsulting::pipeline-runner"
    exp: int
    iat: int

    account_uuid: str = Field(alias="accountUuid")
    workspace_uuid: str = Field(alias="workspaceUuid")
    repository_uuid: str = Field(alias="repositoryUuid")
    pipeline_uuid: str = Field(alias="pipelineUuid")
    step_uuid: str = Field(alias="stepUuid")
    deployment_environment_uuid: str = Field(alias="deploymentEnvironmentUuid")
    branch_name: str = Field(alias="branchName")

    @classmethod
    def new(cls) -> "Self":
        now = datetime.now(tz=UTC)
        iat = int(now.timestamp())
        exp = iat + 3600

        pipeline_uuid = f"{{{uuid.uuid4()}}}"
        deployment_environment_uuid = f"{{{uuid.uuid4()}}}"
        step_uuid = f"{{{uuid.uuid4()}}}"
        sub = f"{pipeline_uuid}:{deployment_environment_uuid}:{step_uuid}"

        return cls(iat=iat, exp=exp, sub=sub)


def main() -> None:
    meta = ProjectMetadata.load_from_file("~/src/foo")
    private_key = serialization.load_pem_private_key(meta.gpg_key.encode(), password=None)

    payload = OidcPayload.new()

    token = jwt.encode(payload.model_dump(), meta.gpg_key, algorithm="RS256")

    print(token)

    # jwk = JWK.from_key(private_key)
    # print(jwk.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
