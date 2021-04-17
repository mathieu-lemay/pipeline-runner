import os
import tarfile
from tempfile import TemporaryDirectory

import pytest

from pipeline_runner import PipelineRunner, config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cache_directory(mocker):
    with TemporaryDirectory() as tempdir:
        m = mocker.patch("pipeline_runner.utils.get_user_cache_directory")

        m.return_value = tempdir

        return tempdir


def test_success():
    runner = PipelineRunner("custom.test_success")
    result = runner.run()

    assert result.ok


def test_failure():
    runner = PipelineRunner("custom.test_failure")
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 69


def test_after_script():
    runner = PipelineRunner("custom.test_after_script")
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 2


def test_cache_alpine(cache_directory):
    runner = PipelineRunner("custom.test_cache_alpine")
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(cache_directory, config.project_env_name, "caches", "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_debian(cache_directory):
    runner = PipelineRunner("custom.test_cache_debian")
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(cache_directory, config.project_env_name, "caches", "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_invalid_cache(cache_directory):
    runner = PipelineRunner("custom.test_invalid_cache")
    result = runner.run()

    assert result.ok

    project_cache_dir = os.path.join(cache_directory, config.project_env_name, "caches")
    assert len(os.listdir(project_cache_dir)) == 0


def test_artifacts(cache_directory):
    runner = PipelineRunner("custom.test_artifacts")
    result = runner.run()

    assert result.ok

    artifacts_dir = os.path.join(
        cache_directory,
        config.project_env_name,
        "pipelines",
        f"{result.build_number}-{result.pipeline_uuid}",
        "artifacts",
    )
    directories = []
    files = []

    for root, ds, fs in os.walk(artifacts_dir):
        for d in ds:
            directories.append(os.path.relpath(os.path.join(root, d), artifacts_dir))

        for f in fs:
            files.append(os.path.relpath(os.path.join(root, f), artifacts_dir))

    assert sorted(directories) == ["valid-folder", "valid-folder/sub"]
    assert sorted(files) == ["file-name", "valid-folder/a", "valid-folder/b", "valid-folder/sub/c"]


def test_deployment():
    runner = PipelineRunner("custom.test_deployment_environment")
    result = runner.run()

    assert result.ok


def test_docker_in_docker(cache_directory):
    runner = PipelineRunner("custom.test_docker_in_docker")
    result = runner.run()

    assert result.exit_code == 0


def test_run_as_user():
    runner = PipelineRunner("custom.test_run_as_user")
    result = runner.run()

    assert result.ok


# def test_environment_variables():
#     assert False
#
#
# def test_pipeline_yml_in_other_folder():
#     assert False
