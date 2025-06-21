import logging
import os.path
from collections.abc import Generator
from pathlib import Path
from time import time

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.logging import LogCaptureFixture
from pytest_mock import MockerFixture

from pipeline_runner.models import ProjectMetadata, Repository


@pytest.fixture(autouse=True)
def faker_seed() -> float:
    return time()


@pytest.fixture
def caplog(caplog: LogCaptureFixture) -> LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture(autouse=True)
def user_cache_directory(tmp_path: Path, mocker: MockerFixture) -> Path:
    cache_dir = tmp_path / "cache"

    m = mocker.patch("pipeline_runner.utils.get_cache_directory")
    m.return_value = str(cache_dir)

    return cache_dir


@pytest.fixture(autouse=True)
def user_data_directory(tmp_path: Path, mocker: MockerFixture) -> Path:
    data_dir = tmp_path / "data"

    m = mocker.patch("pipeline_runner.utils.get_data_directory")
    m.return_value = str(data_dir)

    return data_dir


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
