import concurrent.futures
import io
import os
import tarfile
import time
from tempfile import TemporaryDirectory

import pytest
from tenacity import retry, stop_after_delay, wait_fixed

from pipeline_runner import PipelineRunner, config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cache_directory(mocker):
    with TemporaryDirectory() as tempdir:
        m = mocker.patch("pipeline_runner.utils.get_user_cache_directory")

        m.return_value = tempdir

        yield tempdir

        # FIXME: Remove when using proper docker cache
        docker_cache_dir = os.path.join(tempdir, config.project_env_name, "caches", "docker")
        if os.path.exists(docker_cache_dir):
            import subprocess

            cmd = f"sudo rm -rf {docker_cache_dir}"
            subprocess.run(cmd, shell=True)


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

    assert os.path.exists(os.path.join(cache_directory, "docker.bin"))


def test_run_as_user():
    runner = PipelineRunner("custom.test_run_as_user")
    result = runner.run()

    assert result.ok


def test_pipeline_variables(cache_directory, monkeypatch):
    filename = "some-file"
    message = "Hello World!"

    monkeypatch.setattr("sys.stdin", io.StringIO(f"{filename}\n{message}\n\n"))

    runner = PipelineRunner("custom.test_pipeline_variables")
    result = runner.run()

    assert result.ok

    output_file = os.path.join(
        cache_directory,
        config.project_env_name,
        "pipelines",
        f"{result.build_number}-{result.pipeline_uuid}",
        "artifacts",
        "output",
        filename,
    )

    assert os.path.exists(output_file)

    with open(output_file) as f:
        assert f.read() == f"{message}\n"


@pytest.fixture
def artifacts_directory(cache_directory, mocker):
    build_number = 1
    pipeline_uuid = "cafebabe-beef-dead-1337-123456789012"

    mocker.patch("pipeline_runner.PipelineRunner._get_build_number", return_value=build_number)
    mocker.patch("pipeline_runner.models._generate_id", return_value=pipeline_uuid)

    return os.path.join(
        cache_directory,
        config.project_env_name,
        "pipelines",
        f"{build_number}-{pipeline_uuid}",
        "artifacts",
    )


def test_manual_trigger(artifacts_directory, monkeypatch):
    r, w = os.pipe()

    read_buffer = os.fdopen(r, "r")
    monkeypatch.setattr("sys.stdin", read_buffer)

    setup_done_file = os.path.join(artifacts_directory, "setup_done")

    def _run_pipeline():
        runner = PipelineRunner("custom.test_manual_trigger")
        result = runner.run()

        return result.ok, result.exit_code

    @retry(wait=wait_fixed(0.05), stop=stop_after_delay(5))
    def _wait_for_setup_done():
        assert os.path.exists(setup_done_file)

    def _ensure_still_running(future_, max_wait=5):
        end = time.time() + max_wait
        while time.time() < end:
            assert not future_.done()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_run_pipeline)

        _wait_for_setup_done()

        _ensure_still_running(future)

        with open(w, "w") as write_buffer:
            write_buffer.write("\n")

        res = future.result(timeout=10)

    assert res == (True, 0)


# def test_environment_variables():
#     assert False
#
#
# def test_pipeline_yml_in_other_folder():
#     assert False
