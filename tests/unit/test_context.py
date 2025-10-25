import os.path
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, Mock

import pytest
from faker import Faker
from pytest_mock import MockerFixture

from pipeline_runner.context import PipelineRunContext, StepRunContext
from pipeline_runner.models import (
    CloneSettings,
    Image,
    Options,
    ProjectMetadata,
    Service,
    StepWrapper,
    WorkspaceMetadata,
)
from pipeline_runner.runner import PipelineRunRequest


def test_get_log_directory_returns_the_right_directory(
    user_data_directory: Path, workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    log_directory = os.path.join(
        user_data_directory,
        project_metadata.path_slug,
        "pipelines",
        f"{project_metadata.build_number}-{prc.pipeline_uuid}",
        "logs",
    )
    assert prc.get_log_directory() == log_directory


def test_get_artifact_directory_returns_the_right_directory(
    user_data_directory: Path, workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    artifact_directory = os.path.join(
        user_data_directory,
        project_metadata.path_slug,
        "pipelines",
        f"{project_metadata.build_number}-{prc.pipeline_uuid}",
        "artifacts",
    )
    assert prc.get_artifact_directory() == artifact_directory


def test_get_pipeline_cache_directory_returns_the_right_directory(
    user_cache_directory: Path, workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    cache_directory = os.path.join(user_cache_directory, project_metadata.path_slug, "caches")
    assert prc.get_cache_directory() == cache_directory


def test_docker_is_added_to_services_if_not_present(
    workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    docker_service = Service(
        image=Image(
            name="docker-public.packages.atlassian.com/sox/atlassian"
            "/bitbucket-pipelines-docker-daemon:v25.0.5-tlsfalse-prod-stable"
        ),
        variables={},
        memory=1024,
    )
    assert prc.services == {"docker": docker_service}


def test_docker_service_uses_fallback_values(
    workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={},
        services={"docker": Service(memory=2048, variables={"FOO": "bar"})},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    docker_service = Service(
        image=Image(
            name="docker-public.packages.atlassian.com/sox/atlassian"
            "/bitbucket-pipelines-docker-daemon:v25.0.5-tlsfalse-prod-stable"
        ),
        variables={"FOO": "bar"},
        memory=2048,
    )
    assert prc.services == {"docker": docker_service}


def test_default_caches_are_used(workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={"poetry": "$HOME/.cache/pypoetry"},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    all_caches = {
        "composer": "~/.composer/cache",
        "dotnetcore": "~/.nuget/packages",
        "gradle": "~/.gradle/caches ",
        "ivy2": "~/.ivy2/cache",
        "maven": "~/.m2/repository",
        "node": "node_modules",
        "pip": "~/.cache/pip",
        "sbt": "~/.sbt",
        "poetry": "$HOME/.cache/pypoetry",
    }

    assert prc.caches == all_caches


def test_default_caches_can_be_overridden(
    workspace_metadata: WorkspaceMetadata, project_metadata: ProjectMetadata
) -> None:
    pipeline = Mock()
    repository = Mock()

    prc = PipelineRunContext(
        pipeline_name="custom.test",
        pipeline=pipeline,
        caches={"poetry": "$HOME/.cache/pypoetry", "pip": "foobar"},
        services={},
        clone_settings=CloneSettings.empty(),
        options=Options(),
        workspace_metadata=workspace_metadata,
        project_metadata=project_metadata,
        repository=repository,
    )

    all_caches = {
        "composer": "~/.composer/cache",
        "dotnetcore": "~/.nuget/packages",
        "gradle": "~/.gradle/caches ",
        "ivy2": "~/.ivy2/cache",
        "maven": "~/.m2/repository",
        "node": "node_modules",
        "pip": "foobar",
        "sbt": "~/.sbt",
        "poetry": "$HOME/.cache/pypoetry",
    }

    assert prc.caches == all_caches


def test_pipeline_run_context_from_run_request(
    tmp_path: Path,
    mocker: MockerFixture,
    faker: Faker,
) -> None:
    # Needed because the fake repo path doesn't contain a git repo
    mocker.patch("pipeline_runner.models.Repo")

    pipeline_name = faker.pystr()

    image_name = faker.pystr()
    image_username = faker.user_name()
    image_password = faker.password()
    env_vars = {
        "IMAGE_USERNAME": image_username,
        "IMAGE_PASSWORD": image_password,
    }

    repository_path = tmp_path / "repository"
    repository_path.mkdir()

    pipeline_file = repository_path / "bitbucket-pipelines.yml"
    pipeline_file.write_text(
        dedent(f"""
            pipelines:
              custom:
                {pipeline_name}:
                  - step:
                      image:
                        name: {image_name}
                        username: $IMAGE_USERNAME
                        password: $IMAGE_PASSWORD
                      script:
                        - exit 0
            """)
    )

    selected_steps = faker.words(2)

    env_file = tmp_path / "runner.env"
    with env_file.open("w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")

    run_request = PipelineRunRequest(
        pipeline_name=f"custom.{pipeline_name}",
        repository_path=repository_path.as_posix(),
        selected_steps=selected_steps,
        env_files=[env_file.as_posix()],
    )

    ctx = PipelineRunContext.from_run_request(run_request)

    assert ctx.pipeline_name == f"custom.{pipeline_name}"
    assert ctx.repository.path == repository_path.as_posix()
    assert ctx.env_vars == env_vars
    assert ctx.selected_steps == selected_steps

    steps = ctx.pipeline.get_steps()
    assert len(steps) == 1

    # Type checks
    assert isinstance(steps[0], StepWrapper)
    assert steps[0].step.image is not None

    assert steps[0].step.image.name == image_name
    assert steps[0].step.image.username == image_username
    assert steps[0].step.image.password == image_password


def test_step_run_context_init(faker: Faker) -> None:
    step = MagicMock()
    step.name = faker.pystr()

    pipeline_run_ctx = MagicMock()
    pipeline_run_ctx.project_metadata.path_slug = faker.pystr()

    ctx = StepRunContext(
        step=step,
        pipeline_run_context=pipeline_run_ctx,
    )

    assert step.name.lower() in ctx.slug
    assert pipeline_run_ctx.project_metadata.path_slug in ctx.slug


@pytest.mark.parametrize(
    ("parallel_step_index", "parallel_step_count", "is_valid"),
    [
        (None, None, True),
        (0, 0, True),
        (1, 1, True),
        (None, 0, False),
        (0, None, False),
    ],
)
def test_step_run_context_init_raises_error_on_invalid_parallel_step(
    faker: Faker, parallel_step_index: int | None, parallel_step_count: int | None, is_valid: bool
) -> None:
    step = MagicMock()
    step.name = faker.pystr()

    pipeline_run_ctx = MagicMock()
    pipeline_run_ctx.project_metadata.path_slug = faker.pystr()

    if is_valid:
        StepRunContext(
            step=step,
            pipeline_run_context=pipeline_run_ctx,
            parallel_step_index=parallel_step_index,
            parallel_step_count=parallel_step_count,
        )
    else:
        with pytest.raises(
            ValueError, match="`parallel_step_index` and `parallel_step_count` must be both defined or both undefined"
        ):
            StepRunContext(
                step=step,
                pipeline_run_context=pipeline_run_ctx,
                parallel_step_index=parallel_step_index,
                parallel_step_count=parallel_step_count,
            )


def test_step_run_context_merges_global_options(faker: Faker) -> None:
    step = MagicMock()
    step.name = faker.pystr()
    step.services = faker.words(2)

    pipeline_run_ctx = MagicMock()
    pipeline_run_ctx.options.docker = True
    pipeline_run_ctx.project_metadata.path_slug = faker.pystr()

    assert "docker" not in step.services

    ctx = StepRunContext(
        step=step,
        pipeline_run_context=pipeline_run_ctx,
    )

    assert "docker" in ctx.step.services
