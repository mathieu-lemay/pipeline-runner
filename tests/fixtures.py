import os.path

import pytest

from pipeline_runner.models import ProjectMetadata, Repository


@pytest.fixture(autouse=True)
def user_cache_directory(tmp_path, mocker):
    cache_dir = os.path.join(tmp_path, "cache")

    m = mocker.patch("pipeline_runner.utils.get_cache_directory")
    m.return_value = cache_dir

    yield cache_dir


@pytest.fixture(autouse=True)
def user_data_directory(tmp_path, mocker):
    data_dir = os.path.join(tmp_path, "data")

    m = mocker.patch("pipeline_runner.utils.get_data_directory")
    m.return_value = data_dir

    yield data_dir


@pytest.fixture
def project_metadata(mocker):
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
def repository():
    from pipeline_runner import __file__ as root_file

    return Repository(os.path.dirname(os.path.dirname(root_file)))


@pytest.fixture
def tmp_path_chdir(request, tmp_path):
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(request.config.invocation_dir)
