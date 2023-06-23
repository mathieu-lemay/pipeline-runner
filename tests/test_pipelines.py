import concurrent.futures
import io
import json
import logging.config
import os
import tarfile
import time
import uuid

import docker
import dotenv
import pytest
from tenacity import retry, stop_after_delay, wait_fixed

from pipeline_runner.config import config
from pipeline_runner.models import ProjectMetadata
from pipeline_runner.runner import PipelineRunner, PipelineRunRequest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cwd():
    dir_ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(dir_)


@pytest.fixture(autouse=True)
def _log():
    logging.config.dictConfig(config.log_config)


@pytest.fixture()
def project_path_slug(mocker):
    slug = "project-path-slug"
    mocker.patch("pipeline_runner.utils.hashify_path", return_value=slug)

    return slug


@pytest.fixture()
def project_data_directory(user_data_directory, project_path_slug, mocker):
    project_data_directory = os.path.join(user_data_directory, project_path_slug)

    mocker.patch("pipeline_runner.utils.get_project_data_directory", return_value=project_data_directory)

    return project_data_directory


@pytest.fixture()
def pipeline_data_directory(project_data_directory, mocker):
    build_number = 1
    pipeline_uuid = "cafebabe-beef-dead-1337-123456789012"

    pipeline_data_directory = os.path.join(project_data_directory, "pipelines", f"{build_number}-{pipeline_uuid}")

    mocker.patch(
        "pipeline_runner.context.PipelineRunContext.get_pipeline_data_directory", return_value=pipeline_data_directory
    )

    return pipeline_data_directory


@pytest.fixture(autouse=True)
def artifacts_directory(pipeline_data_directory, mocker):
    artifacts_directory = os.path.join(pipeline_data_directory, "artifacts")
    os.makedirs(artifacts_directory)

    mocker.patch("pipeline_runner.context.PipelineRunContext.get_artifact_directory", return_value=artifacts_directory)

    return artifacts_directory


@pytest.fixture(autouse=True)
def project_cache_directory(user_cache_directory, mocker):
    project_cache = os.path.join(user_cache_directory, "caches")
    os.makedirs(project_cache)

    mocker.patch("pipeline_runner.context.PipelineRunContext.get_cache_directory", return_value=project_cache)

    yield project_cache

    docker_client = docker.from_env()
    cache_volume = next(
        (v for v in docker_client.volumes.list() if v.name == "pipeline-runner-service-docker-cache"), None
    )
    if cache_volume:
        cache_volume.remove()


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


def test_default_cache(project_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_default_cache"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "pip.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["pip/MD5SUM", "pip/a", "pip/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_alpine(project_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_alpine"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_debian(project_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_debian"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_invalid_cache(project_cache_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_invalid_cache"))
    result = runner.run()

    assert result.ok

    assert len(os.listdir(project_cache_directory)) == 0


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
    var_with_default_1 = "Overriding default 1"
    var_with_choice = "staging"

    monkeypatch.setattr(
        "sys.stdin", io.StringIO(f"{filename}\n{message}\n\n{var_with_default_1}\n\n{var_with_choice}\n\n")
    )

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
        assert f.read() == "\n".join(
            [
                f"Message: {message}",
                f"Var With Default 1: {var_with_default_1}",
                "Var With Default 2: Default 2",
                f"Var With Choice: {var_with_choice}",
                "Var With Choice Using Default: ghi",
                "",
            ]
        )


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


def test_environment_variables(artifacts_directory, project_metadata, repository, mocker):
    pipeline_uuid = "pipeline-uuid"
    step_uuid = "step-uuid"

    def uuid_generator(real_uuid_fn):
        yield pipeline_uuid
        yield step_uuid
        while True:
            yield real_uuid_fn()

    real_uuid4 = uuid.uuid4

    uuid4 = mocker.patch("uuid.uuid4")
    uuid4.side_effect = uuid_generator(real_uuid4)

    runner = PipelineRunner(PipelineRunRequest("custom.test_environment_variables"))
    result = runner.run()

    assert result.ok

    pipeline_env_file = os.path.join(artifacts_directory, "variables")
    assert os.path.exists(pipeline_env_file)

    variables = {
        k: v
        for k, v in dotenv.dotenv_values(pipeline_env_file).items()
        if k.startswith("BITBUCKET") or k in ("BUILD_DIR", "CI")
    }

    slug = project_metadata.slug
    expected = {
        "BITBUCKET_BRANCH": repository.get_current_branch(),
        "BITBUCKET_BUILD_NUMBER": str(project_metadata.build_number),
        "BITBUCKET_CLONE_DIR": "/opt/atlassian/pipelines/agent/build",
        "BITBUCKET_COMMIT": repository.get_current_commit(),
        "BITBUCKET_PIPELINE_UUID": str(pipeline_uuid),
        "BITBUCKET_PROJECT_KEY": project_metadata.key,
        "BITBUCKET_PROJECT_UUID": str(project_metadata.project_uuid),
        "BITBUCKET_REPO_FULL_NAME": f"{slug}/{slug}",
        "BITBUCKET_REPO_IS_PRIVATE": "true",
        "BITBUCKET_REPO_OWNER": config.username,
        "BITBUCKET_REPO_OWNER_UUID": config.owner_uuid,
        "BITBUCKET_REPO_SLUG": slug,
        "BITBUCKET_REPO_UUID": str(project_metadata.repo_uuid),
        "BITBUCKET_STEP_UUID": str(step_uuid),
        "BITBUCKET_WORKSPACE": slug,
        "BUILD_DIR": "/opt/atlassian/pipelines/agent/build",
        "CI": "true",
    }

    assert variables == expected


def test_project_metadata_is_generated_if_file_doesnt_exist(project_data_directory, artifacts_directory):
    runner = PipelineRunner(PipelineRunRequest("custom.test_environment_variables"))
    result = runner.run()

    assert result.ok

    project_metadata_file = os.path.join(project_data_directory, "meta.json")
    assert os.path.exists(project_metadata_file)

    pipeline_env_file = os.path.join(artifacts_directory, "variables")
    assert os.path.exists(pipeline_env_file)

    project_metadata = ProjectMetadata.parse_file(project_metadata_file)

    slug = project_metadata.slug
    expected = {
        "BITBUCKET_BUILD_NUMBER": str(project_metadata.build_number),
        "BITBUCKET_PROJECT_KEY": project_metadata.key,
        "BITBUCKET_PROJECT_UUID": str(project_metadata.project_uuid),
        "BITBUCKET_REPO_FULL_NAME": f"{slug}/{slug}",
        "BITBUCKET_REPO_SLUG": slug,
        "BITBUCKET_REPO_UUID": str(project_metadata.repo_uuid),
        "BITBUCKET_WORKSPACE": slug,
    }

    variables = {k: v for k, v in dotenv.dotenv_values(pipeline_env_file).items() if k in expected}

    assert variables == expected


def test_project_metadata_is_read_from_file_if_it_exists(project_data_directory, artifacts_directory):
    project_metadata_file = os.path.join(project_data_directory, "meta.json")
    project_metadata = {
        "name": "Some Project",
        "path_slug": "some-project-CAFEBABE",
        "slug": "some-project",
        "key": "SP",
        "project_uuid": str(uuid.uuid4()),
        "repo_uuid": str(uuid.uuid4()),
        "build_number": 68,
    }

    with open(project_metadata_file, "w") as f:
        json.dump(project_metadata, f)

    runner = PipelineRunner(PipelineRunRequest("custom.test_environment_variables"))
    result = runner.run()

    assert result.ok

    pipeline_env_file = os.path.join(artifacts_directory, "variables")
    assert os.path.exists(pipeline_env_file)

    project_metadata = ProjectMetadata.parse_obj(project_metadata)

    slug = project_metadata.slug
    expected = {
        "BITBUCKET_BUILD_NUMBER": str(project_metadata.build_number + 1),
        "BITBUCKET_PROJECT_KEY": project_metadata.key,
        "BITBUCKET_PROJECT_UUID": str(project_metadata.project_uuid),
        "BITBUCKET_REPO_FULL_NAME": f"{slug}/{slug}",
        "BITBUCKET_REPO_SLUG": slug,
        "BITBUCKET_REPO_UUID": str(project_metadata.repo_uuid),
        "BITBUCKET_WORKSPACE": slug,
    }

    variables = {k: v for k, v in dotenv.dotenv_values(pipeline_env_file).items() if k in expected}

    assert variables == expected


def test_pipeline_with_pipe(pipeline_data_directory, project_path_slug, monkeypatch):
    runner = PipelineRunner(PipelineRunRequest("custom.test_pipe"))
    result = runner.run()

    assert result.ok

    log_file = os.path.join(pipeline_data_directory, "logs", f"{project_path_slug}-step-test.txt")
    with open(log_file) as f:
        log_lines = f.readlines()

    for expected in ("name\n", "'name-in-single-quotes'\n", '"name-in-double-quotes"\n'):
        assert any(i for i in log_lines if i == expected)


def test_ssh_key_is_present_in_runner():
    runner = PipelineRunner(PipelineRunRequest("custom.test_ssh_key"))
    result = runner.run()

    assert result.ok


def test_pipeline_supports_buildkit():
    runner = PipelineRunner(PipelineRunRequest("custom.test_docker_buildkit"))
    result = runner.run()

    assert result.ok
