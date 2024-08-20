import concurrent.futures
import hashlib
import io
import json
import logging.config
import os
import tarfile
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future
from pathlib import Path
from uuid import UUID, uuid4

import docker  # type: ignore[import-untyped]
import dotenv
import pytest
from _pytest.config import Config as PytestConfig
from _pytest.monkeypatch import MonkeyPatch
from faker import Faker
from pytest_mock import MockerFixture
from tenacity import retry, stop_after_delay, wait_fixed

from pipeline_runner.cache import compute_cache_key
from pipeline_runner.config import config
from pipeline_runner.models import ProjectMetadata, Repository
from pipeline_runner.runner import PipelineRunner, PipelineRunRequest


@pytest.fixture(autouse=True)
def _cwd() -> None:
    dir_ = Path(__file__).parent.parent.parent
    os.chdir(dir_)


@pytest.fixture(autouse=True)
def _log() -> None:
    logging.config.dictConfig(config.log_config)


@pytest.fixture
def project_path_slug(mocker: MockerFixture) -> str:
    slug = "project-path-slug"
    mocker.patch("pipeline_runner.utils.hashify_path", return_value=slug)

    return slug


@pytest.fixture
def project_data_directory(user_data_directory: Path, project_path_slug: str, mocker: MockerFixture) -> Path:
    project_data_directory = user_data_directory / project_path_slug

    mocker.patch("pipeline_runner.utils.get_project_data_directory", return_value=str(project_data_directory))

    return project_data_directory


@pytest.fixture
def pipeline_data_directory(project_data_directory: Path, mocker: MockerFixture) -> Path:
    build_number = 1
    pipeline_uuid = "cafebabe-beef-dead-1337-123456789012"

    pipeline_data_directory = project_data_directory / "pipelines" / f"{build_number}-{pipeline_uuid}"

    mocker.patch(
        "pipeline_runner.context.PipelineRunContext.get_pipeline_data_directory", return_value=pipeline_data_directory
    )

    return pipeline_data_directory


@pytest.fixture(autouse=True)
def artifacts_directory(pipeline_data_directory: Path, mocker: MockerFixture) -> Path:
    artifacts_directory = pipeline_data_directory / "artifacts"
    os.makedirs(artifacts_directory)

    mocker.patch(
        "pipeline_runner.context.PipelineRunContext.get_artifact_directory", return_value=str(artifacts_directory)
    )

    return artifacts_directory


@pytest.fixture(autouse=True)
def project_cache_directory(user_cache_directory: Path, mocker: MockerFixture) -> Generator[Path, None, None]:
    project_cache = user_cache_directory / "caches"
    os.makedirs(project_cache)

    mocker.patch("pipeline_runner.context.PipelineRunContext.get_cache_directory", return_value=str(project_cache))

    yield project_cache

    docker_client = docker.from_env()
    cache_volume = next(
        (v for v in docker_client.volumes.list() if v.name == "pipeline-runner-service-docker-cache"), None
    )
    if cache_volume:
        cache_volume.remove()


@pytest.fixture
def custom_cache_key_file(pytestconfig: PytestConfig) -> Generator[Path, None, None]:
    path = pytestconfig.rootpath / "custom-cache-key"

    yield path

    path.unlink(missing_ok=True)


def test_success() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_success"))
    result = runner.run()

    assert result.ok


def test_failure() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_failure"))
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 69


def test_after_script() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_after_script"))
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 2


def test_default_cache(project_cache_directory: Path) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_default_cache"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "pip.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["pip/MD5SUM", "pip/a", "pip/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_alpine(project_cache_directory: Path) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_alpine"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_cache_debian(project_cache_directory: Path) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_cache_debian"))
    result = runner.run()

    assert result.ok, "Pipeline failed"

    cache_file = os.path.join(project_cache_directory, "service1.tar")

    assert os.path.isfile(cache_file), f"Cache file not found: {cache_file}"

    with tarfile.open(cache_file) as f:
        files_in_tar = [i.path for i in f.getmembers() if i.isfile()]

    expected_files = ["service1/MD5SUM", "service1/a", "service1/b"]
    assert sorted(files_in_tar) == expected_files


def test_invalid_cache(project_cache_directory: Path) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_invalid_cache"))
    result = runner.run()

    assert result.ok

    assert len(os.listdir(project_cache_directory)) == 0


def test_custom_cache(project_cache_directory: Path, custom_cache_key_file: Path, faker: Faker) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_custom_cache"))
    cache_name = "custom"

    file_data = faker.pystr()
    custom_cache_key_file.write_text(file_data)
    expected_hash = _sha256hash(file_data.encode())

    result = runner.run()
    assert result.ok

    assert (project_cache_directory / f"{cache_name}-{expected_hash}.tar").exists()

    # Updating the key file should create a new cach
    file_data = faker.pystr()
    custom_cache_key_file.write_text(file_data)
    expected_hash = _sha256hash(file_data.encode())

    # Clear the lru cache to simulate an entirely new run.
    compute_cache_key.cache_clear()

    result = runner.run()
    assert result.ok

    assert (project_cache_directory / f"{cache_name}-{expected_hash}.tar").exists()


def test_artifacts(artifacts_directory: Path) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_artifacts"))
    result = runner.run()

    assert result.ok

    directories = []
    files = []

    for root, ds, fs in os.walk(artifacts_directory):
        directories += [os.path.relpath(os.path.join(root, d), artifacts_directory) for d in ds]
        files += [os.path.relpath(os.path.join(root, f), artifacts_directory) for f in fs]

    assert sorted(directories) == ["valid-folder", "valid-folder/sub"]
    assert sorted(files) == ["file-name", "valid-folder/a", "valid-folder/b", "valid-folder/sub/c"]


def test_deployment() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_deployment_environment"))
    result = runner.run()

    assert result.ok


def test_service() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_service"))
    result = runner.run()

    assert result.exit_code == 0


def test_docker_in_docker() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_docker_in_docker"))
    result = runner.run()

    assert result.exit_code == 0


def test_run_as_user() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_run_as_user"))
    result = runner.run()

    assert result.ok


def test_pipeline_variables(artifacts_directory: Path, monkeypatch: MonkeyPatch) -> None:
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


def test_manual_trigger(artifacts_directory: Path, monkeypatch: MonkeyPatch) -> None:
    r, w = os.pipe()

    read_buffer = os.fdopen(r, "r")
    monkeypatch.setattr("sys.stdin", read_buffer)

    setup_done_file = artifacts_directory / "setup_done"

    def _run_pipeline() -> tuple[bool, int]:
        runner = PipelineRunner(PipelineRunRequest("custom.test_manual_trigger"))
        result = runner.run()

        return result.ok, result.exit_code

    @retry(wait=wait_fixed(0.05), stop=stop_after_delay(5))
    def _wait_for_setup_done() -> None:
        assert setup_done_file.exists()

    def _ensure_still_running(future_: Future[tuple[bool, int]], max_wait: int = 5) -> None:
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


def test_parallel_steps() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_parallel_steps"))
    result = runner.run()

    assert result.ok


def test_environment_variables(
    artifacts_directory: Path, project_metadata: ProjectMetadata, repository: Repository, mocker: MockerFixture
) -> None:
    pipeline_uuid = uuid4()
    step_uuid = uuid4()

    def uuid_generator(real_uuid_fn: Callable[[], UUID]) -> Generator[UUID, None, None]:
        yield pipeline_uuid
        yield step_uuid
        while True:
            yield real_uuid_fn()

    real_uuid4 = uuid4

    uuid4_mock = mocker.patch("uuid.uuid4")
    uuid4_mock.side_effect = uuid_generator(real_uuid4)

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
        "BITBUCKET_REPO_OWNER_UUID": str(project_metadata.owner_uuid),
        "BITBUCKET_REPO_SLUG": slug,
        "BITBUCKET_REPO_UUID": str(project_metadata.repo_uuid),
        "BITBUCKET_STEP_UUID": str(step_uuid),
        "BITBUCKET_WORKSPACE": slug,
        "BUILD_DIR": "/opt/atlassian/pipelines/agent/build",
        "CI": "true",
    }

    assert variables == expected


def test_project_metadata_is_generated_if_file_doesnt_exist(
    project_data_directory: Path, artifacts_directory: Path
) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_environment_variables"))
    result = runner.run()

    assert result.ok

    project_metadata_file = Path(project_data_directory) / "meta.json"
    assert project_metadata_file.exists()

    pipeline_env_file = Path(artifacts_directory) / "variables"
    assert pipeline_env_file.exists()

    project_metadata = ProjectMetadata.model_validate_json(project_metadata_file.read_text())

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


def test_project_metadata_is_read_from_file_if_it_exists(
    project_data_directory: Path, artifacts_directory: Path
) -> None:
    project_metadata_file = os.path.join(project_data_directory, "meta.json")
    project_metadata_json = {
        "name": "Some Project",
        "path_slug": "some-project-CAFEBABE",
        "slug": "some-project",
        "key": "SP",
        "project_uuid": str(uuid4()),
        "repo_uuid": str(uuid4()),
        "build_number": 68,
    }

    with open(project_metadata_file, "w") as f:
        json.dump(project_metadata_json, f)

    runner = PipelineRunner(PipelineRunRequest("custom.test_environment_variables"))
    result = runner.run()

    assert result.ok

    pipeline_env_file = os.path.join(artifacts_directory, "variables")
    assert os.path.exists(pipeline_env_file)

    project_metadata = ProjectMetadata.model_validate(project_metadata_json)

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


def test_pipeline_with_pipe(pipeline_data_directory: Path, project_path_slug: str) -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_pipe"))
    result = runner.run()

    assert result.ok

    log_file = os.path.join(pipeline_data_directory, "logs", f"{project_path_slug}-step-test.txt")
    with open(log_file) as f:
        log_lines = f.readlines()

    for expected in ("name\n", "'name-in-single-quotes'\n", '"name-in-double-quotes"\n'):
        assert any(i for i in log_lines if i == expected)


def test_ssh_key_is_present_in_runner() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_ssh_key"))
    result = runner.run()

    assert result.ok


def test_pipeline_supports_buildkit() -> None:
    runner = PipelineRunner(PipelineRunRequest("custom.test_docker_buildkit"))
    result = runner.run()

    assert result.ok


def _sha256hash(data: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(data)

    return hasher.hexdigest()
