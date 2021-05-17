import os.path
from unittest.mock import Mock

import pytest

from pipeline_runner import PipelineRunContext
from pipeline_runner.models import CloneSettings, Pipeline, PipelineInfo
from pipeline_runner.repository import Repository


@pytest.fixture
def repo_metadata(mocker):
    repo_metadata = PipelineInfo(build_number=42)

    mocker.patch("pipeline_runner.context.PipelineRunContext.load_repository_metadata", return_value=repo_metadata)
    mocker.patch("pipeline_runner.context.PipelineRunContext.save_repository_metadata")

    return repo_metadata


def test_get_log_directory_returns_the_right_directory(user_data_directory, repo_metadata):
    pipeline = Mock(spec=Pipeline)
    repository = Mock(spec=Repository, env_name="my-repo-with-hash")

    prc = PipelineRunContext(
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.default(),
        default_image=None,
        repository=repository,
        env_files=[],
        selected_steps=[],
    )

    log_directory = os.path.join(
        user_data_directory,
        repository.env_name,
        "pipelines",
        f"{repo_metadata.build_number}-{prc.pipeline_uuid}",
        "logs",
    )
    assert prc.get_log_directory() == log_directory


def test_get_artifact_directory_returns_the_right_directory(user_data_directory, repo_metadata):
    pipeline = Mock(spec=Pipeline)
    repository = Mock(spec=Repository, env_name="my-repo-with-hash")

    prc = PipelineRunContext(
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.default(),
        default_image=None,
        repository=repository,
        env_files=[],
        selected_steps=[],
    )

    artifact_directory = os.path.join(
        user_data_directory,
        repository.env_name,
        "pipelines",
        f"{repo_metadata.build_number}-{prc.pipeline_uuid}",
        "artifacts",
    )
    assert prc.get_artifact_directory() == artifact_directory


def test_get_pipeline_cache_directory_returns_the_right_directory(user_cache_directory, repo_metadata):
    pipeline = Mock(spec=Pipeline)
    repository = Mock(spec=Repository, env_name="my-repo-with-hash")

    prc = PipelineRunContext(
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.default(),
        default_image=None,
        repository=repository,
        env_files=[],
        selected_steps=[],
    )

    cache_directory = os.path.join(user_cache_directory, repository.env_name, "caches")
    assert prc.get_pipeline_cache_directory() == cache_directory
