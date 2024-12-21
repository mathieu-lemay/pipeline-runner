from textwrap import dedent
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from pipeline_runner.config import config
from pipeline_runner.models import (
    AwsCredentials,
    Definitions,
    Image,
    ParallelStep,
    ParallelSteps,
    Pipe,
    Pipeline,
    Pipelines,
    PipelineSpec,
    Service,
    Step,
    StepSize,
    StepWrapper,
    Trigger,
    Variable,
    Variables,
)


def test_parse_empty_definitions() -> None:
    defs = Definitions.model_validate({})

    assert defs.caches == {}
    assert defs.services == {}


def test_parse_caches() -> None:
    caches = {
        "poetry": "~/.cache/pypoetry",
        "pip": "${HOME}/.cache/pip",
    }

    value = {"caches": caches}

    defs = Definitions.model_validate(value)

    assert defs.caches == caches


def test_parse_definitions() -> None:
    services = {
        "docker": {"memory": 3072},
        "postgres": {
            "image": "postgres:13",
            "variables": {
                "POSTGRES_DB": "pg-db",
                "POSTGRES_USER": "pg-user",
                "POSTGRES_PASSWORD": "pg-passwd",
            },
        },
        "mysql": {
            "image": "mysql",
            "environment": {
                "MYSQL_DB": "my-db",
                "MYSQL_USER": "my-user",
                "MYSQL_PASSWORD": "my-passwd",
            },
        },
    }

    value = {"services": services}

    defs = Definitions.model_validate(value)

    services = {
        "docker": Service(image=None, variables={}, memory=3072),
        "postgres": Service(
            image=Image(name="postgres:13"),
            variables={
                "POSTGRES_DB": "pg-db",
                "POSTGRES_USER": "pg-user",
                "POSTGRES_PASSWORD": "pg-passwd",
            },
            memory=config.service_container_default_memory_limit,
        ),
        "mysql": Service(
            image=Image(name="mysql"),
            variables={
                "MYSQL_DB": "my-db",
                "MYSQL_USER": "my-user",
                "MYSQL_PASSWORD": "my-passwd",
            },
            memory=config.service_container_default_memory_limit,
        ),
    }

    assert defs.services == services


def test_parse_image() -> None:
    name = "alpine:latest"
    user = 1000

    value = {"name": name, "run-as-user": user}

    image = Image.model_validate(value)

    assert image == Image(name=name, run_as_user="1000")


def test_parse_image_with_credentials() -> None:
    name = "private-repo/image"
    username = "my-username"
    password = "my-password"
    email = "my-email"

    value = {"name": name, "username": username, "password": password, "email": email}

    assert Image.model_validate(value) == Image(name=name, username=username, password=password, email=email)


def test_parse_image_with_aws_credentials() -> None:
    name = "aws-repo/image"
    access_key_id = "access-key-id"
    secret_access_key = "secret-access-key"

    value = {"name": name, "aws": {"access-key": access_key_id, "secret-key": secret_access_key}}
    image = Image.model_validate(value)

    assert image == Image(
        name=name, aws=AwsCredentials(access_key_id=access_key_id, secret_access_key=secret_access_key)
    )


def test_parse_image_with_aws_oidc_role() -> None:
    name = "alpine:latest"
    oidc_role = "some-role"

    value = {"name": name, "aws": {"oidc-role": oidc_role}}

    with pytest.raises(ValidationError) as exc_info:
        Image.model_validate(value)

    assert "aws oidc-role not supported" in str(exc_info.value)


def test_parse_image_with_envvars() -> None:
    name = "alpine:latest"
    username = "my-username"
    password = "my-password"
    email = "my-email"
    access_key_id = "access-key-id"
    secret_access_key = "secret-access-key"

    value = {
        "name": "${IMAGE_NAME}",
        "username": "$REPO_USERNAME",
        "password": "$REPO_PASSWORD",
        "email": "$REPO_EMAIL",
        "aws": {"access-key": "$AWS_ACCESS_KEY_ID", "secret-key": "$AWS_SECRET_ACCESS_KEY"},
    }

    env_vars = {
        "IMAGE_NAME": name,
        "REPO_USERNAME": username,
        "REPO_PASSWORD": password,
        "REPO_EMAIL": email,
        "AWS_ACCESS_KEY_ID": access_key_id,
        "AWS_SECRET_ACCESS_KEY": secret_access_key,
    }

    image = Image.model_validate(value)
    image.expand_env_vars(env_vars)

    expected = Image(
        name="${IMAGE_NAME}",  # Env vars in the name field are not expanded
        username=username,
        password=password,
        email=email,
        aws=AwsCredentials(access_key_id=access_key_id, secret_access_key=secret_access_key),
    )

    assert image == expected


def test_parse_all_types_of_pipelines() -> None:
    steps = [{"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}}]
    spec = {
        "pipelines": {
            "default": steps,
            "custom": {
                "custom1": steps,
            },
            "branches": {
                "branch1": steps,
            },
            "pull-requests": {
                "pr1": steps,
            },
            "tags": {
                "tag1": steps,
            },
            "bookmarks": {
                "bookmark1": steps,
            },
        }
    }

    pipelines = PipelineSpec.model_validate(spec)

    expected_pipeline = Pipeline(root=[StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))])
    expected_pipelines = PipelineSpec(
        pipelines=Pipelines(
            default=expected_pipeline,
            branches={"branch1": expected_pipeline},
            pull_requests={"pr1": expected_pipeline},
            custom={"custom1": expected_pipeline},
            tags={"tag1": expected_pipeline},
            bookmarks={"bookmark1": expected_pipeline},
        ),
    )

    assert pipelines == expected_pipelines


def test_parse_pipeline_with_steps() -> None:
    spec = [
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
        {"step": {"name": "Step 2", "script": ["echo 'Step 2'"]}},
    ]

    pipeline = Pipeline.model_validate(spec)

    step1 = StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))
    step2 = StepWrapper(step=Step(name="Step 2", script=["echo 'Step 2'"]))
    expected = Pipeline(root=[step1, step2])

    assert pipeline == expected


def test_parse_pipeline_with_parallel_steps() -> None:
    spec = [
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
        {
            "parallel": [
                {"step": {"name": "Parallel Step 1", "script": ["echo 'Parallel 1'"]}},
                {"step": {"name": "Parallel Step 2", "script": ["echo 'Parallel 2'"]}},
            ]
        },
        {
            "parallel": {
                "steps": [
                    {"step": {"name": "Parallel Step 3", "script": ["echo 'Parallel 3'"]}},
                    {"step": {"name": "Parallel Step 4", "script": ["echo 'Parallel 4'"]}},
                ]
            }
        },
    ]

    pipeline = Pipeline.model_validate(spec)

    step1 = StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))
    pstep1 = StepWrapper(step=Step(name="Parallel Step 1", script=["echo 'Parallel 1'"]))
    pstep2 = StepWrapper(step=Step(name="Parallel Step 2", script=["echo 'Parallel 2'"]))
    pstep3 = StepWrapper(step=Step(name="Parallel Step 3", script=["echo 'Parallel 3'"]))
    pstep4 = StepWrapper(step=Step(name="Parallel Step 4", script=["echo 'Parallel 4'"]))
    expected = Pipeline(
        root=[
            step1,
            ParallelStep(parallel=[pstep1, pstep2]),
            ParallelStep(parallel=ParallelSteps(wrapped=[pstep3, pstep4])),
        ]
    )

    assert pipeline == expected


def test_parse_pipeline_with_variables() -> None:
    spec = [
        {"variables": [{"name": "foo"}, {"name": "bar"}]},
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
    ]

    pipeline = Pipeline.model_validate(spec)

    variables = Variables(wrapped=[Variable(name="foo"), Variable(name="bar")])
    step = StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))
    expected = Pipeline(root=[variables, step])

    assert pipeline == expected


def test_variables_can_only_be_the_first_element_of_the_pipelines() -> None:
    spec = [
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
        {"variables": [{"name": "foo"}, {"name": "bar"}]},
    ]

    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(spec)

    assert exc_info.value.title == "Pipeline"
    assert exc_info.value.error_count() == 1

    assert exc_info.value.errors()[0]["loc"] == ()
    assert exc_info.value.errors()[0]["msg"] == "Value error, 'variables' can only be the first element of the list."
    assert exc_info.value.errors()[0]["type"] == "value_error"


def test_parse_variables_with_default_values() -> None:
    spec = [
        {"variables": [{"name": "foo", "default": "foo-value"}, {"name": "bar"}]},
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
    ]

    pipeline = Pipeline.model_validate(spec)

    variables = Variables(wrapped=[Variable(name="foo", default="foo-value"), Variable(name="bar", default=None)])
    step = StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))
    expected = Pipeline(root=[variables, step])

    assert pipeline == expected


def test_parse_variables_with_choices() -> None:
    spec = [
        {"variables": [{"name": "foo", "allowed-values": ["a1", "b2", "c3"], "default": "a1"}]},
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
    ]

    pipeline = Pipeline.model_validate(spec)

    variables = Variables(wrapped=[Variable(name="foo", allowed_values=["a1", "b2", "c3"], default="a1")])
    step = StepWrapper(step=Step(name="Step 1", script=["cat /etc/os-release", "exit 0"]))
    expected = Pipeline(root=[variables, step])

    assert pipeline == expected


def test_variables_with_choices_must_have_a_default_value() -> None:
    spec = [
        {"variables": [{"name": "foo", "allowed-values": ["a", "b", "c"]}]},
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
    ]

    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(spec)

    assert exc_info.value.title == "Pipeline"
    expected_msg = (
        "Value error, The variable default value is not provided. "
        "A default value is required if allowed values list is specified."
    )
    assert any(
        e["msg"] == expected_msg and e["loc"] == (0, "Variables", "variables", 0) for e in exc_info.value.errors()
    )


def test_variables_with_choices_must_have_a_default_value_that_is_part_of_the_choices() -> None:
    spec = [
        {"variables": [{"name": "foo", "allowed-values": ["a", "b", "c"], "default": "d"}]},
        {"step": {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}},
    ]

    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(spec)

    assert exc_info.value.title == "Pipeline"
    expected_msg = 'Value error, The variable allowed values list doesn\'t contain a default value "d".'
    assert any(
        e["msg"] == expected_msg and e["loc"] == (0, "Variables", "variables", 0) for e in exc_info.value.errors()
    )


def test_parse_step_with_default_values() -> None:
    spec = {"name": "Step 1", "script": ["cat /etc/os-release", "exit 0"]}

    step = Step.model_validate(spec)

    assert step == Step(name="Step 1", script=["cat /etc/os-release", "exit 0"])


def test_parse_step_with_manual_trigger() -> None:
    spec = {"script": [], "trigger": "manual"}

    step = Step.model_validate(spec)

    assert step.trigger == Trigger.Manual


@pytest.mark.parametrize("size", list(StepSize))
def test_parse_step_size(size: StepSize) -> None:
    spec = {"script": [], "size": size.value}

    step = Step.model_validate(spec)

    assert step.size == size


def test_parse_step_with_pipes() -> None:
    spec: dict[str, Any] = {
        "script": [
            "echo a",
            {
                "pipe": "atlassian/trigger-pipeline:4.2.1",
                "variables": {
                    "BITBUCKET_USERNAME": "${TRIGGER_PIPELINE_USERNAME}",
                    "BITBUCKET_APP_PASSWORD": "${TRIGGER_PIPELINE_APP_PASSWORD}",
                    "REPOSITORY": "other-repo",
                    "CUSTOM_PIPELINE_NAME": "deploy",
                    "PIPELINE_VARIABLES": (
                        '[{"key": "PIPELINE_VAR_1", "value": "VALUE_1"}, '
                        '{ "key": "PIPELINE_VAR_2", "value": "VALUE_2"}, '
                        '{ "key": "PIPELINE_VAR_3", "value": "VALUE_3"}]'
                    ),
                    "WAIT": "true",
                },
            },
            "echo b",
        ],
        "after-script": [
            "echo c",
            {
                "pipe": "atlassian/trigger-pipeline:4.2.1",
                "variables": {
                    "BITBUCKET_USERNAME": "${TRIGGER_PIPELINE_USERNAME}",
                    "BITBUCKET_APP_PASSWORD": "${TRIGGER_PIPELINE_APP_PASSWORD}",
                },
            },
            "echo d",
        ],
    }

    parsed = Step.model_validate(spec)

    pipe_a = Pipe(
        pipe="atlassian/trigger-pipeline:4.2.1",
        variables=spec["script"][1]["variables"],
    )

    pipe_b = Pipe(
        pipe="atlassian/trigger-pipeline:4.2.1",
        variables=spec["after-script"][1]["variables"],
    )

    assert parsed.script == ["echo a", pipe_a, "echo b"]
    assert parsed.after_script == ["echo c", pipe_b, "echo d"]


def test_parse_pipeline_with_env_vars() -> None:
    step_image = "step-image"
    service_image = "service-image"
    parallel_step_image = "parallel-image"

    spec = {
        "definitions": {"services": {"from_env": {"image": service_image, "variables": {"PASSWORD": "$PASSWORD"}}}},
        "pipelines": {
            "default": [
                {
                    "step": {
                        "name": "Test image from env",
                        "image": step_image,
                        "services": ["from_env"],
                        "script": ["cat /etc/os-release"],
                    },
                },
                {
                    "parallel": [
                        {
                            "step": {
                                "name": "Parallel 1",
                                "image": parallel_step_image,
                                "services": ["from_env"],
                                "script": ["cat /etc/os-release"],
                            }
                        },
                        {
                            "step": {
                                "name": "Parallel 2",
                                "image": parallel_step_image,
                                "services": ["from_env"],
                                "script": ["cat /etc/os-release"],
                            }
                        },
                    ],
                },
            ]
        },
    }

    password = "some-password"
    variables = {
        "PASSWORD": password,
    }

    parsed = PipelineSpec.model_validate(spec)
    parsed.expand_env_vars(variables)

    expected = {
        "image": None,
        "definitions": {
            "caches": {},
            "services": {
                "from_env": {
                    "image": {
                        "name": service_image,
                        "username": None,
                        "password": None,
                        "email": None,
                        "run-as-user": None,
                        "aws": None,
                    },
                    "environment": {"PASSWORD": password},
                    "memory": 1024,
                }
            },
        },
        "clone": {"depth": None, "lfs": None, "enabled": None},
        "pipelines": {
            "default": [
                {
                    "step": {
                        "name": "Test image from env",
                        "script": ["cat /etc/os-release"],
                        "image": {
                            "name": step_image,
                            "username": None,
                            "password": None,
                            "email": None,
                            "run-as-user": None,
                            "aws": None,
                        },
                        "caches": [],
                        "services": ["from_env"],
                        "artifacts": [],
                        "after-script": [],
                        "size": StepSize.Size1,
                        "clone": {"depth": None, "lfs": None, "enabled": None},
                        "deployment": None,
                        "trigger": Trigger.Automatic,
                        "max-time": None,
                        "condition": None,
                        "oidc": False,
                    },
                },
                {
                    "parallel": [
                        {
                            "step": {
                                "name": "Parallel 1",
                                "script": ["cat /etc/os-release"],
                                "image": {
                                    "name": parallel_step_image,
                                    "username": None,
                                    "password": None,
                                    "email": None,
                                    "run-as-user": None,
                                    "aws": None,
                                },
                                "caches": [],
                                "services": ["from_env"],
                                "artifacts": [],
                                "after-script": [],
                                "size": StepSize.Size1,
                                "clone": {"depth": None, "lfs": None, "enabled": None},
                                "deployment": None,
                                "trigger": Trigger.Automatic,
                                "max-time": None,
                                "condition": None,
                                "oidc": False,
                            }
                        },
                        {
                            "step": {
                                "name": "Parallel 2",
                                "script": ["cat /etc/os-release"],
                                "image": {
                                    "name": parallel_step_image,
                                    "username": None,
                                    "password": None,
                                    "email": None,
                                    "run-as-user": None,
                                    "aws": None,
                                },
                                "caches": [],
                                "services": ["from_env"],
                                "artifacts": [],
                                "after-script": [],
                                "size": StepSize.Size1,
                                "clone": {"depth": None, "lfs": None, "enabled": None},
                                "deployment": None,
                                "trigger": Trigger.Automatic,
                                "max-time": None,
                                "condition": None,
                                "oidc": False,
                            }
                        },
                    ],
                },
            ],
            "branches": {},
            "pull-requests": {},
            "custom": {},
            "tags": {},
            "bookmarks": {},
        },
    }

    assert parsed.model_dump(by_alias=True) == expected


def test_parse_pipeline_with_anchors() -> None:
    yaml_str = dedent(
        """
        ---
        definitions:
          steps:
            - step: &build-test
                name: Build and test
                script:
                  - mvn package
                artifacts:
                  - target/**

        pipelines:
          branches:
            develop:
              - step: *build-test
            main:
              - step: *build-test
        """
    )

    pipelines_data = yaml.safe_load(yaml_str)
    model = PipelineSpec.model_validate(pipelines_data)

    steps = model.pipelines.branches["develop"].get_steps()
    assert len(steps) == 1
    assert isinstance(steps[0], StepWrapper)
    assert steps[0].step.name == "Build and test"

    assert model.pipelines.branches["develop"] == model.pipelines.branches["main"]
