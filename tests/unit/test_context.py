import os.path
from pathlib import Path
from unittest.mock import Mock

from pipeline_runner.context import PipelineRunContext
from pipeline_runner.models import CloneSettings, Image, ProjectMetadata, Service, WorkspaceMetadata


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
        default_image=None,
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
        default_image=None,
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
        default_image=None,
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
        default_image=None,
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
        default_image=None,
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
        default_image=None,
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
        default_image=None,
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
