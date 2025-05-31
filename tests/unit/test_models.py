import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from faker import Faker
from pydantic import ValidationError

from pipeline_runner import utils
from pipeline_runner.models import (
    Artifacts,
    Cache,
    CacheKey,
    Definitions,
    ParallelStep,
    ParallelSteps,
    Pipe,
    Pipeline,
    PipelineResult,
    Pipelines,
    ProjectMetadata,
    Service,
    Step,
    StepWrapper,
)


@pytest.fixture
def ssh_rsa_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private_key.decode()


def test_model_extra_keys_are_ignored() -> None:
    spec: dict[str, Any] = {
        "default": [
            {
                "step": {
                    "Invalid": 42,
                    "script": ["echo hello"],
                },
            },
        ],
        "invalid": "invalid",
    }

    expected = Pipelines(default=Pipeline(root=[StepWrapper(step=Step(script=["echo hello"]))]))

    assert Pipelines.model_validate(spec) == expected


def test_cache_supports_static_name() -> None:
    spec: dict[str, Any] = {"caches": {"some-cache": "some-path"}}

    caches = Definitions.model_validate(spec).caches

    assert caches == {"some-cache": "some-path"}


def test_cache_supports_custom_keys() -> None:
    spec: dict[str, Any] = {
        "caches": {
            "better-cache": {
                "key": {
                    "files": ["file1.txt", "file2.txt"],
                },
                "path": "some-path",
            }
        },
    }

    caches = Definitions.model_validate(spec).caches

    assert caches == {
        "better-cache": Cache(
            key=CacheKey(files=["file1.txt", "file2.txt"]),
            path="some-path",
        )
    }


def test_definitions_ensures_non_default_services_have_an_image(faker: Faker) -> None:
    svc_name = faker.pystr()
    spec: dict[str, Any] = {
        "services": {svc_name: {}},
    }

    with pytest.raises(ValidationError) as err_ctx:
        Definitions.model_validate(spec)

    assert err_ctx.value.error_count() == 1

    error = err_ctx.value.errors()[0]
    assert error == {
        "type": "missing",
        "loc": ("services", svc_name, "image"),
        "msg": f"Service '{svc_name}' must have an image",
        "input": Service(image=None, variables={}),
        "ctx": {"service_name": svc_name},
    }


@pytest.mark.parametrize(("num_items", "is_valid"), [(0, False), (1, True), (2, True)])
def test_parallel_steps_must_contain_at_least_1_item(num_items: int, is_valid: bool) -> None:
    spec: dict[str, Any] = {"steps": [{"step": {"script": []}} for _ in range(num_items)]}

    if is_valid:
        ParallelSteps.model_validate(spec)
    else:
        with pytest.raises(ValidationError, match="List should have at least 1 item after validation"):
            ParallelSteps.model_validate(spec)


@pytest.mark.parametrize(("num_items", "is_valid"), [(0, False), (1, True), (2, True)])
def test_parallel_step_must_contain_at_least_1_item(num_items: int, is_valid: bool) -> None:
    spec: dict[str, Any] = {"parallel": [{"step": {"script": []}} for _ in range(num_items)]}

    if is_valid:
        ParallelStep.model_validate(spec)
    else:
        with pytest.raises(ValidationError, match="Value should have at least 1 item after validation"):
            ParallelStep.model_validate(spec)


def test_pipeline_result_ok_returns_true_if_exit_code_is_zero() -> None:
    build_number = 33
    pipeline_uuid = uuid4()
    res = PipelineResult(0, build_number, pipeline_uuid)

    assert res.ok


def test_pipeline_result_ok_returns_false_if_exit_code_is_not_zero() -> None:
    build_number = 33
    pipeline_uuid = uuid4()

    for _ in range(1, 256):
        res = PipelineResult(1, build_number, pipeline_uuid)

        assert res.ok is False


def test_pipe_as_cmd_transforms_the_pipe_into_a_docker_command() -> None:
    p = Pipe(
        pipe="foo/bar:1.2.3",
    )

    assert p.as_cmd() == (
        "docker run --rm "
        "--volume=/opt/atlassian/pipelines/agent/build:/opt/atlassian/pipelines/agent/build "
        "--volume=/opt/atlassian/pipelines/agent/ssh:/opt/atlassian/pipelines/agent/ssh:ro "
        "--volume=/opt/atlassian/pipelines/bin/docker:/usr/local/bin/docker:ro "
        "--workdir=$(pwd) "
        "--label=org.bitbucket.pipelines.system=true "
        '--env=BITBUCKET_STEP_TRIGGERER_UUID="$BITBUCKET_STEP_TRIGGERER_UUID" '
        '--env=BITBUCKET_REPO_FULL_NAME="$BITBUCKET_REPO_FULL_NAME" '
        '--env=BITBUCKET_GIT_HTTP_ORIGIN="$BITBUCKET_GIT_HTTP_ORIGIN" '
        '--env=BITBUCKET_REPO_SLUG="$BITBUCKET_REPO_SLUG" '
        '--env=BITBUCKET_PROJECT_UUID="$BITBUCKET_PROJECT_UUID" '
        '--env=CI="$CI" '
        '--env=BITBUCKET_REPO_OWNER="$BITBUCKET_REPO_OWNER" '
        '--env=BITBUCKET_REPO_IS_PRIVATE="$BITBUCKET_REPO_IS_PRIVATE" '
        '--env=BITBUCKET_WORKSPACE="$BITBUCKET_WORKSPACE" '
        '--env=BITBUCKET_SSH_KEY_FILE="$BITBUCKET_SSH_KEY_FILE" '
        '--env=BITBUCKET_REPO_OWNER_UUID="$BITBUCKET_REPO_OWNER_UUID" '
        '--env=BITBUCKET_STEP_RUN_NUMBER="$BITBUCKET_STEP_RUN_NUMBER" '
        '--env=BITBUCKET_BUILD_NUMBER="$BITBUCKET_BUILD_NUMBER" '
        '--env=BITBUCKET_BRANCH="$BITBUCKET_BRANCH" '
        '--env=BITBUCKET_GIT_SSH_ORIGIN="$BITBUCKET_GIT_SSH_ORIGIN" '
        '--env=BITBUCKET_PIPELINE_UUID="$BITBUCKET_PIPELINE_UUID" '
        '--env=BITBUCKET_PIPELINES_VARIABLES_PATH="$BITBUCKET_PIPELINES_VARIABLES_PATH" '
        '--env=BITBUCKET_COMMIT="$BITBUCKET_COMMIT" '
        '--env=BITBUCKET_REPO_UUID="$BITBUCKET_REPO_UUID" '
        '--env=BITBUCKET_CLONE_DIR="$BITBUCKET_CLONE_DIR" '
        '--env=BITBUCKET_PROJECT_KEY="$BITBUCKET_PROJECT_KEY" '
        '--env=PIPELINES_JWT_TOKEN="$PIPELINES_JWT_TOKEN" '
        '--env=BITBUCKET_STEP_UUID="$BITBUCKET_STEP_UUID" '
        '--env=BITBUCKET_DOCKER_HOST_INTERNAL="$BITBUCKET_DOCKER_HOST_INTERNAL" '
        '--env=DOCKER_HOST="tcp://host.docker.internal:2375" '
        "foo/bar:1.2.3"
    )


def test_pipe_as_cmd_adds_variables_as_docker_env_vars() -> None:
    p = Pipe(
        pipe="foo/bar:1.2.3",
        variables={
            "FOO": "BAR",
            "BAZ": '[{"some": "json with \'single-quotes\'", "more": "json with line\nbreak"}]',
            "ENV": "${SOME_ENVVAR}",
            "EXTRA_ARGS": ["a", "b"],
        },
    )

    assert p.as_cmd() == (
        "docker run --rm "
        "--volume=/opt/atlassian/pipelines/agent/build:/opt/atlassian/pipelines/agent/build "
        "--volume=/opt/atlassian/pipelines/agent/ssh:/opt/atlassian/pipelines/agent/ssh:ro "
        "--volume=/opt/atlassian/pipelines/bin/docker:/usr/local/bin/docker:ro "
        "--workdir=$(pwd) "
        "--label=org.bitbucket.pipelines.system=true "
        '--env=BITBUCKET_STEP_TRIGGERER_UUID="$BITBUCKET_STEP_TRIGGERER_UUID" '
        '--env=BITBUCKET_REPO_FULL_NAME="$BITBUCKET_REPO_FULL_NAME" '
        '--env=BITBUCKET_GIT_HTTP_ORIGIN="$BITBUCKET_GIT_HTTP_ORIGIN" '
        '--env=BITBUCKET_REPO_SLUG="$BITBUCKET_REPO_SLUG" '
        '--env=BITBUCKET_PROJECT_UUID="$BITBUCKET_PROJECT_UUID" '
        '--env=CI="$CI" '
        '--env=BITBUCKET_REPO_OWNER="$BITBUCKET_REPO_OWNER" '
        '--env=BITBUCKET_REPO_IS_PRIVATE="$BITBUCKET_REPO_IS_PRIVATE" '
        '--env=BITBUCKET_WORKSPACE="$BITBUCKET_WORKSPACE" '
        '--env=BITBUCKET_SSH_KEY_FILE="$BITBUCKET_SSH_KEY_FILE" '
        '--env=BITBUCKET_REPO_OWNER_UUID="$BITBUCKET_REPO_OWNER_UUID" '
        '--env=BITBUCKET_STEP_RUN_NUMBER="$BITBUCKET_STEP_RUN_NUMBER" '
        '--env=BITBUCKET_BUILD_NUMBER="$BITBUCKET_BUILD_NUMBER" '
        '--env=BITBUCKET_BRANCH="$BITBUCKET_BRANCH" '
        '--env=BITBUCKET_GIT_SSH_ORIGIN="$BITBUCKET_GIT_SSH_ORIGIN" '
        '--env=BITBUCKET_PIPELINE_UUID="$BITBUCKET_PIPELINE_UUID" '
        '--env=BITBUCKET_PIPELINES_VARIABLES_PATH="$BITBUCKET_PIPELINES_VARIABLES_PATH" '
        '--env=BITBUCKET_COMMIT="$BITBUCKET_COMMIT" '
        '--env=BITBUCKET_REPO_UUID="$BITBUCKET_REPO_UUID" '
        '--env=BITBUCKET_CLONE_DIR="$BITBUCKET_CLONE_DIR" '
        '--env=BITBUCKET_PROJECT_KEY="$BITBUCKET_PROJECT_KEY" '
        '--env=PIPELINES_JWT_TOKEN="$PIPELINES_JWT_TOKEN" '
        '--env=BITBUCKET_STEP_UUID="$BITBUCKET_STEP_UUID" '
        '--env=BITBUCKET_DOCKER_HOST_INTERNAL="$BITBUCKET_DOCKER_HOST_INTERNAL" '
        '--env=DOCKER_HOST="tcp://host.docker.internal:2375" '
        '-e FOO="BAR" '
        '-e BAZ="[{\\"some\\": \\"json with \'single-quotes\'\\", \\"more\\": \\"json with line\nbreak\\"}]" '
        '-e ENV="${SOME_ENVVAR}" '
        '-e EXTRA_ARGS_0="a" '
        '-e EXTRA_ARGS_1="b" '
        '-e EXTRA_ARGS_COUNT="2" '
        "foo/bar:1.2.3"
    )


def test_pipe_as_cmd_uses_proper_repo_for_atlassian_pipes(faker: Faker) -> None:
    image = faker.pystr()
    tag = faker.pystr()

    p = Pipe(
        pipe=f"atlassian/{image}:{tag}",
    )

    cmd = p.as_cmd()

    assert cmd.endswith(f"bitbucketpipelines/{image}:{tag}")


def test_pipe_expand_variables_expands_list_variables() -> None:
    p = Pipe(
        pipe="atlassian/foo:1.2.3",
        variables={
            "STRING_VARIABLE": "some-string,with-commas",
            "LIST_VARIABLE": ["list", "of", "values"],
        },
    )

    assert p.expand_variables() == {
        "STRING_VARIABLE": "some-string,with-commas",
        "LIST_VARIABLE_0": "list",
        "LIST_VARIABLE_1": "of",
        "LIST_VARIABLE_2": "values",
        "LIST_VARIABLE_COUNT": "3",
    }


def test_pipe_get_image_returns_its_name_as_docker_image() -> None:
    p = Pipe(pipe="foo/bar:1.2.3", variables={})

    assert p.get_image() == "foo/bar:1.2.3"


def test_pipe_get_image_returns_the_right_docker_image_if_pipe_is_from_atlassian() -> None:
    p = Pipe(pipe="atlassian/bar:1.2.3", variables={})

    assert p.get_image() == "bitbucketpipelines/bar:1.2.3"


def test_step_artifacts_parses_list() -> None:
    spec: list[str] = ["foo", "bar", "baz"]

    artifacts = Artifacts.model_validate(spec)

    assert artifacts == Artifacts(paths=["foo", "bar", "baz"], download=True)


def test_step_artifacts_parses_object() -> None:
    spec: dict[str, Any] = {"paths": ["foo", "bar", "baz"], "download": False}

    artifacts = Artifacts.model_validate(spec)

    assert artifacts == Artifacts(paths=["foo", "bar", "baz"], download=False)


def test_step_condition_is_optional() -> None:
    spec: dict[str, Any] = {"script": []}

    step = Step.model_validate(spec)

    assert step.condition is None


def test_step_condition_must_include_changesets() -> None:
    spec: dict[str, Any] = {"script": [], "condition": {}}

    with pytest.raises(ValidationError) as err_ctx:
        Step.model_validate(spec)

    error = next((e for e in err_ctx.value.errors() if e["loc"] == ("condition", "changesets")), None)
    assert error is not None
    assert error["type"] == "missing"


@pytest.mark.parametrize(
    ("changesets", "expected_error_type"),
    [
        ({}, "missing"),
        ({"includePaths": []}, "too_short"),
        ({"includePaths": ["a-path"]}, None),
        ({"includePaths": ["a-path", "another-path"]}, None),
    ],
)
def test_step_condition_changesets_must_contain_at_least_one_include_path(
    changesets: dict[str, Any], expected_error_type: str | None
) -> None:
    spec: dict[str, Any] = {"script": [], "condition": {"changesets": changesets}}

    if expected_error_type is not None:
        with pytest.raises(ValidationError) as err_ctx:
            Step.model_validate(spec)

        error = next(
            (e for e in err_ctx.value.errors() if e["loc"] == ("condition", "changesets", "includePaths")), None
        )
        assert error is not None
        assert error["type"] == expected_error_type
    else:
        Step.model_validate(spec)


def test_project_metadata_load_from_file_generates_new_metadata_if_not_exists(
    user_data_directory: Path, faker: Faker
) -> None:
    project_name = "Some project name"
    project_directory = f"{faker.pystr()}/{faker.pystr()}/{project_name}"
    expected_path_slug = utils.hashify_path(project_directory)

    metadata = ProjectMetadata.load_from_file(project_directory)

    assert metadata == ProjectMetadata(
        name=project_name,
        path_slug=expected_path_slug,
        slug="some-project-name",
        key="SPN",
        owner_uuid=metadata.owner_uuid,
        project_uuid=metadata.project_uuid,
        repo_uuid=metadata.repo_uuid,
        build_number=1,
        ssh_key=metadata.ssh_key,
    )

    # Ensure UUIDs are valid
    assert isinstance(metadata.owner_uuid, UUID)
    assert isinstance(metadata.project_uuid, UUID)
    assert isinstance(metadata.repo_uuid, UUID)

    # Ensure ssh key is valid
    serialization.load_pem_private_key(metadata.ssh_key.encode(), password=None)

    metadata_file = user_data_directory / expected_path_slug / "meta.json"
    assert metadata_file.exists()

    saved_metadata = ProjectMetadata.model_validate_json(metadata_file.read_text())
    assert saved_metadata == metadata


def test_project_metadata_load_from_file_loads_existing_metadata(
    user_data_directory: Path, ssh_rsa_key: str, faker: Faker
) -> None:
    project_name = "Some project name"
    project_directory = f"{faker.pystr()}/{faker.pystr()}/{project_name}"
    expected_path_slug = utils.hashify_path(project_directory)

    name = faker.pystr()
    path_slug = faker.pystr()
    slug = faker.pystr()
    key = faker.pystr()
    owner_uuid = uuid4()
    project_uuid = uuid4()
    repo_uuid = uuid4()
    last_build_number = faker.pyint(min_value=100, max_value=999)

    existing_metadata = {
        "name": name,
        "path_slug": path_slug,
        "slug": slug,
        "key": key,
        "owner_uuid": str(owner_uuid),
        "project_uuid": str(project_uuid),
        "repo_uuid": str(repo_uuid),
        "build_number": last_build_number,
        "ssh_key": ssh_rsa_key,
    }

    metadata_file = user_data_directory / expected_path_slug / "meta.json"
    metadata_file.parent.mkdir(parents=True)
    metadata_file.write_text(json.dumps(existing_metadata))

    expected_metadata = ProjectMetadata(
        name=name,
        path_slug=path_slug,
        slug=slug,
        key=key,
        owner_uuid=owner_uuid,
        project_uuid=project_uuid,
        repo_uuid=repo_uuid,
        build_number=last_build_number + 1,
        ssh_key=ssh_rsa_key,
    )

    assert ProjectMetadata.load_from_file(project_directory) == expected_metadata


def test_project_metadata_load_from_file_fills_missing_values(user_data_directory: Path, faker: Faker) -> None:
    project_name = "Some project name"
    project_directory = f"{faker.pystr()}/{faker.pystr()}/{project_name}"
    expected_path_slug = utils.hashify_path(project_directory)

    name = faker.pystr()
    path_slug = faker.pystr()
    slug = faker.pystr()
    key = faker.pystr()

    existing_metadata = {"name": name, "path_slug": path_slug, "slug": slug, "key": key}

    metadata_file = user_data_directory / expected_path_slug / "meta.json"
    metadata_file.parent.mkdir(parents=True)
    metadata_file.write_text(json.dumps(existing_metadata))

    # Just make sure it loaded properly and we didn't get any validation errors
    ProjectMetadata.load_from_file(project_directory)
