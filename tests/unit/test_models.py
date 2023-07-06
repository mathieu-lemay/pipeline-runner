import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline_runner.models import ParallelStep, Pipe, PipelineResult


@pytest.mark.parametrize(("num_items", "is_valid"), [(0, False), (1, False), (2, True)])
def test_parallel_step_must_contain_at_least_2_items(num_items: int, is_valid: bool) -> None:
    spec: dict[str, Any] = {"parallel": [{"step": {"script": []}} for _ in range(num_items)]}

    if is_valid:
        ParallelStep.model_validate(spec)
    else:
        with pytest.raises(ValidationError, match="List should have at least 2 items after validation"):
            ParallelStep.model_validate(spec)


def test_pipeline_result_ok_returns_true_if_exit_code_is_zero() -> None:
    build_number = 33
    pipeline_uuid = uuid.uuid4()
    res = PipelineResult(0, build_number, pipeline_uuid)

    assert res.ok


def test_pipeline_result_ok_returns_false_if_exit_code_is_not_zero() -> None:
    build_number = 33
    pipeline_uuid = uuid.uuid4()

    for _ in range(1, 256):
        res = PipelineResult(1, build_number, pipeline_uuid)

        assert res.ok is False


def test_pipe_get_image_returns_its_name_as_docker_image() -> None:
    p = Pipe(pipe="foo/bar:1.2.3", variables={})

    assert p.get_image() == "foo/bar:1.2.3"


def test_pipe_get_image_returns_the_right_docker_image_if_pipe_is_from_atlassian() -> None:
    p = Pipe(pipe="atlassian/bar:1.2.3", variables={})

    assert p.get_image() == "bitbucketpipelines/bar:1.2.3"


def test_pipe_as_cmd_transforms_the_pipe_into_a_docker_command() -> None:
    p = Pipe(
        pipe="atlassian/foo:1.2.3",
        variables={
            "FOO": "BAR",
            "BAZ": '[{"some": "json with \'single-quotes\'", "more": "json with line\nbreak"}]',
            "ENV": "${SOME_ENVVAR}",
        },
    )

    assert p.as_cmd() == (
        'docker run --rm -e FOO="BAR" '
        '-e BAZ="[{\\"some\\": \\"json with \'single-quotes\'\\", \\"more\\": \\"json with line\nbreak\\"}]" '
        '-e ENV="${SOME_ENVVAR}" bitbucketpipelines/foo:1.2.3'
    )
