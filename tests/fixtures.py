import logging
import os.path
import uuid
from collections.abc import Generator
from pathlib import Path
from time import time
from unittest.mock import Mock

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.logging import LogCaptureFixture
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest_mock import MockerFixture

from pipeline_runner import config as config_module
from pipeline_runner.config import Config
from pipeline_runner.models import ProjectMetadata, Repository, WorkspaceMetadata


@pytest.fixture(autouse=True)
def faker_seed() -> float:
    return time()


@pytest.fixture
def caplog(caplog: LogCaptureFixture) -> LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture
def config(mocker: MockerFixture) -> Config:
    mock_config = Mock()

    # Set a few default values
    mock_config.volumes = []

    mocker.patch.object(config_module, "_config", mock_config)
    return mock_config


@pytest.fixture(autouse=True)
def user_cache_directory(tmp_path: Path, mocker: MockerFixture) -> Path:
    cache_dir = tmp_path / "cache"

    m = mocker.patch("pipeline_runner.utils.get_cache_directory")
    m.return_value = str(cache_dir)

    return cache_dir


@pytest.fixture(autouse=True)
def user_data_directory(tmp_path: Path, mocker: MockerFixture) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    m = mocker.patch("pipeline_runner.utils.get_data_directory")
    m.return_value = str(data_dir)

    return data_dir


@pytest.fixture
def ssh_rsa_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private_key.decode()


@pytest.fixture
def oidc_private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private_key.decode()


@pytest.fixture
def workspace_metadata(mocker: MockerFixture, oidc_private_key: str) -> WorkspaceMetadata:
    workspace_metadata = WorkspaceMetadata(
        workspace_uuid=uuid.uuid4(),
        owner_uuid=uuid.uuid4(),
        oidc_private_key=oidc_private_key,
    )

    mocker.patch("pipeline_runner.models.WorkspaceMetadata.load_from_file", return_value=workspace_metadata)

    return workspace_metadata


@pytest.fixture
def project_metadata(mocker: MockerFixture) -> ProjectMetadata:
    project_metadata = ProjectMetadata(
        name="SomeNiceProject",
        slug="some-nice-project",
        key="SNP",
        path_slug="some-nice-project-FOOBAR",
        build_number=451,
    )

    mocker.patch("pipeline_runner.models.ProjectMetadata.load_from_file", return_value=project_metadata)

    return project_metadata


@pytest.fixture
def repository() -> Repository:
    from pipeline_runner import __file__ as root_file  # noqa: PLC0415  # Import should be at top of file

    return Repository(os.path.dirname(os.path.dirname(root_file)))


@pytest.fixture
def tmp_path_chdir(request: FixtureRequest, tmp_path: Path) -> Generator[Path, None, None]:
    """Get a temporary path and change current working directory to it."""
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(request.config.invocation_dir)  # type: ignore[attr-defined]
