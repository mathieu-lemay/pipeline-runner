import logging
from unittest.mock import Mock

import jwt

from pipeline_runner.models import ProjectMetadata, Repository
from pipeline_runner.oidc import OIDCPayload

logger = logging.getLogger(__name__)


def main() -> None:
    meta = ProjectMetadata.load_from_file("~/src/foo")

    repo = Repository("~/src/private/pipeline-runner")
    ctx = Mock()
    ctx.pipeline_ctx.repository = repo
    payload = OIDCPayload.new(ctx)

    print(payload.model_dump_json(indent=2))

    token = jwt.encode(payload.model_dump(), meta.gpg_key, algorithm="RS256")


if __name__ == "__main__":
    main()
