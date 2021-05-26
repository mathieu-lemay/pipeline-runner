import concurrent.futures
import io
import os
import tarfile
import time

import pytest
from tenacity import retry, stop_after_delay, wait_fixed

from pipeline_runner.runner import PipelineRunner, PipelineRunRequest

pytestmark = pytest.mark.integration


@pytest.fixture
def pipeline_data_directory(user_data_directory):
    build_number = 1
    pipeline_uuid = "cafebabe-beef-dead-1337-123456789012"

    pipeline_data_directory = os.path.join(user_data_directory, "pipelines", f"{build_number}-{pipeline_uuid}")

    return pipeline_data_directory


@pytest.fixture(autouse=True)
def artifacts_directory(pipeline_data_directory, mocker):
    artifacts_directory = os.path.join(pipeline_data_directory, "artifacts")
    os.makedirs(artifacts_directory)

    mocker.patch("pipeline_runner.context.PipelineRunContext.get_artifact_directory", return_value=artifacts_directory)

    return artifacts_directory


@pytest.fixture(autouse=True)
def pipeline_cache_directory(user_cache_directory, mocker):
    pipeline_cache = os.path.join(user_cache_directory, "caches")
    os.makedirs(pipeline_cache)

    mocker.patch("pipeline_runner.context.PipelineRunContext.get_pipeline_cache_directory", return_value=pipeline_cache)

    yield pipeline_cache

    docker_cache_dir = os.path.join(pipeline_cache, "docker")
    if os.path.exists(docker_cache_dir):
        import subprocess

        cmd = f"sudo rm -rf {docker_cache_dir}"
        subprocess.run(cmd, shell=True)


def test_success():
    runner = PipelineRunner(PipelineRunRequest("custom.test_success"))
    result = runner.run()

    assert result.ok


def test_failure():
    runner = PipelineRunner(PipelineRunRequest("custom.test_failure"))
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 69


def test_after_script():
    runner = PipelineRunner(PipelineRunRequest("custom.test_after_script"))
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 2


def test_default_cache(pipeline_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_default_cache"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(pipeline_cache_directory, "pip.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["pip/MD5SUM", "pip/a", "pip/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_alpine(pipeline_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_alpine"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(pipeline_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_debian(pipeline_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_debian"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(pipeline_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_invalid_cache(pipeline_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_invalid_cache"))
    result = runner.run()

    assert result.ok

    assert len(os.listdir(pipeline_cache_directory)) == 0


def test_artifacts(artifacts_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_artifacts"))
    result = runner.run()

    assert result.ok

    directories = []
    files = []

    for root, ds, fs in os.walk(artifacts_directory):
        for d in ds:
            directories.append(os.path.relpath(os.path.join(root, d), artifacts_directory))

        for f in fs:
            files.append(os.path.relpath(os.path.join(root, f), artifacts_directory))

    assert sorted(directories) == ["valid-folder", "valid-folder/sub"]
    assert sorted(files) == ["file-name", "valid-folder/a", "valid-folder/b", "valid-folder/sub/c"]


def test_deployment():
    runner = PipelineRunner(PipelineRunRequest("custom.test_deployment_environment"))
    result = runner.run()

    assert result.ok


def test_service():
    runner = PipelineRunner(PipelineRunRequest("custom.test_service"))
    result = runner.run()

    assert result.exit_code == 0


def test_docker_in_docker():
    runner = PipelineRunner(PipelineRunRequest("custom.test_docker_in_docker"))
    result = runner.run()

    assert result.exit_code == 0


def test_run_as_user():
    runner = PipelineRunner(PipelineRunRequest("custom.test_run_as_user"))
    result = runner.run()

    assert result.ok


def test_pipeline_variables(artifacts_directory, monkeypatch):
    filename = "some-file"
    message = "Hello World!"

    monkeypatch.setattr("sys.stdin", io.StringIO(f"{filename}\n{message}\n\n"))

    runner = PipelineRunner(PipelineRunRequest("custom.test_pipeline_variables"))
    result = runner.run()

    assert result.ok

    output_file = os.path.join(
        artifacts_directory,
        "output",
        filename,
    )

    assert os.path.exists(output_file)

    with open(output_file) as f:
        assert f.read() == f"{message}\n"


def test_manual_trigger(artifacts_directory, monkeypatch):
    r, w = os.pipe()

    read_buffer = os.fdopen(r, "r")
    monkeypatch.setattr("sys.stdin", read_buffer)

    setup_done_file = os.path.join(artifacts_directory, "setup_done")

    def _run_pipeline():
        runner = PipelineRunner(PipelineRunRequest("custom.test_manual_trigger"))
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


def test_parallel_steps():
    runner = PipelineRunner(PipelineRunRequest("custom.test_parallel_steps"))
    result = runner.run()

    assert result.ok


# def test_environment_variables():
#     assert False
#
#
# def test_pipeline_yml_in_other_folder():
#     assert False
