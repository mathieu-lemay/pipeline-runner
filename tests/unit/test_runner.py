import concurrent.futures
import os
import time
from collections.abc import Generator
from concurrent.futures import Future
from logging import Logger
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch
from click import UsageError
from docker import DockerClient  # type: ignore[import-untyped]
from faker.proxy import Faker
from pytest_mock import MockerFixture

from pipeline_runner.context import PipelineRunContext
from pipeline_runner.models import Stage, StepWrapper, Trigger
from pipeline_runner.runner import StageRunner, StepRunner


@pytest.fixture(autouse=True)
def docker_client(mocker: MockerFixture) -> Generator[DockerClient]:
    mock_client = mocker.Mock()
    with mocker.patch("pipeline_runner.runner.docker.from_env", return_value=mock_client):
        yield mock_client


@pytest.fixture(autouse=True)
def output_logger(mocker: MockerFixture) -> Generator[Logger]:
    mock_logger = mocker.Mock()
    with mocker.patch("pipeline_runner.runner.utils.get_output_logger", return_value=mock_logger):
        yield mock_logger


def test_step_runner_extract_output_variables(mocker: MockerFixture, faker: Faker, tmp_path: Path) -> None:
    var1 = faker.pystr()
    value1 = faker.pystr()
    var2 = faker.pystr()
    value2 = faker.pystr()
    var3 = faker.pystr()

    existing_var1 = faker.pystr()
    existing_value1 = faker.pystr()
    existing_var2 = faker.pystr()
    existing_value2 = faker.pystr()

    step = mocker.MagicMock(output_variables=[var1, var2, var3])

    pipeline_variables = {
        existing_var1: existing_value1,
        existing_var2: existing_value2,
    }
    pipeline_ctx = mocker.MagicMock(
        pipeline_variables=pipeline_variables,
    )

    vars_file = tmp_path / "vars.env"
    vars_file.write_text(f"{var1}={value1}\n{var2}={value2}\n")

    step_ctx = mocker.MagicMock(
        step=step,
        pipeline_ctx=pipeline_ctx,
    )

    runner = StepRunner(step_ctx)
    runner._pipeline_variables_file = vars_file

    runner._extract_output_variables()

    expected_variables = {
        existing_var1: existing_value1,
        existing_var2: existing_value2,
        var1: value1,
        var2: value2,
    }

    assert pipeline_ctx.pipeline_variables == expected_variables


def test_step_runner_extract_output_variables_overrides_existing_variables(
    mocker: MockerFixture, faker: Faker, tmp_path: Path
) -> None:
    existing_var1 = faker.pystr()
    existing_value1 = faker.pystr()
    existing_var2 = faker.pystr()
    existing_value2 = faker.pystr()

    new_var1 = faker.pystr()
    new_value1 = faker.pystr()
    new_value2 = faker.pystr()

    step = mocker.MagicMock(output_variables=[new_var1, existing_var2])

    pipeline_variables = {
        existing_var1: existing_value1,
        existing_var2: existing_value2,
    }
    pipeline_ctx = mocker.MagicMock(
        pipeline_variables=pipeline_variables,
    )

    vars_file = tmp_path / "vars.env"
    vars_file.write_text(f"{new_var1}={new_value1}\n{existing_var2}={new_value2}\n")

    step_ctx = mocker.MagicMock(
        step=step,
        pipeline_ctx=pipeline_ctx,
    )

    runner = StepRunner(step_ctx)
    runner._pipeline_variables_file = vars_file

    runner._extract_output_variables()

    expected_variables = {
        existing_var1: existing_value1,
        existing_var2: new_value2,
        new_var1: new_value1,
    }

    assert pipeline_ctx.pipeline_variables == expected_variables


def test_step_runner_extract_output_variables_raises_an_error_on_unknown_variables(
    mocker: MockerFixture, faker: Faker, tmp_path: Path
) -> None:
    var1 = faker.pystr()
    value1 = faker.pystr()
    var2 = faker.pystr()
    value2 = faker.pystr()
    var3 = faker.pystr()
    value3 = faker.pystr()
    var4 = faker.pystr()

    step = mocker.MagicMock(output_variables=[var1, var4])

    vars_file = tmp_path / "vars.env"
    vars_file.write_text(f"{var1}={value1}\n{var2}={value2}\n{var3}={value3}\n")

    step_ctx = mocker.MagicMock(step=step)

    runner = StepRunner(step_ctx)
    runner._pipeline_variables_file = vars_file

    with pytest.raises(UsageError) as err_ctx:
        runner._extract_output_variables()

    assert var1 not in err_ctx.value.message
    assert var2 in err_ctx.value.message
    assert var3 in err_ctx.value.message


def test_step_runner_extract_output_variables_raises_an_error_on_invalid_variables(
    mocker: MockerFixture, faker: Faker, tmp_path: Path
) -> None:
    var = faker.pystr()
    value = faker.pystr()

    step = mocker.MagicMock(output_variables=[var])

    vars_file = tmp_path / "vars.env"
    vars_file.write_text(f"VALID_BUT_EMPTY=\nNOT_A_VALID_VAR\n{var}={value}\n")

    step_ctx = mocker.MagicMock(step=step)

    runner = StepRunner(step_ctx)
    runner._pipeline_variables_file = vars_file

    with pytest.raises(UsageError, match="Invalid variable format: NOT_A_VALID_VAR"):
        runner._extract_output_variables()


def test_step_runner_extract_output_variables_does_nothing_if_no_variables_set(mocker: MockerFixture) -> None:
    step = mocker.MagicMock(output_variables=[])

    pipeline_ctx = mocker.MagicMock()
    pipeline_ctx.pipeline_variables.update.side_effect = Exception("Should not be called")

    step_ctx = mocker.MagicMock(
        step=step,
        pipeline_ctx=pipeline_ctx,
    )

    runner = StepRunner(step_ctx)
    runner._pipeline_variables_file = None

    runner._extract_output_variables()


def test_stage_runner_runs_all_steps_of_stage(mocker: MockerFixture) -> None:
    ctx = MagicMock(spec=PipelineRunContext, selected_stages=[])

    step1 = MagicMock(spec=StepWrapper)
    step2 = MagicMock(spec=StepWrapper)

    # Use model_construct to skip validations
    stage = Stage.model_construct(steps=[step1, step2])

    mock_runner = MagicMock(spec=StepRunner)
    mock_runner.run.return_value = 0

    mock_factory = mocker.patch("pipeline_runner.runner.StepRunnerFactory")
    mock_factory.get.return_value = mock_runner

    runner = StageRunner(stage, ctx)
    exit_code = runner.run()

    assert exit_code == 0

    assert mock_factory.get.call_count == 2
    mock_factory.get.assert_any_call(step1, ctx)
    mock_factory.get.assert_any_call(step2, ctx)

    assert mock_runner.run.call_count == 2


def test_stage_runner_stops_on_first_failure(mocker: MockerFixture) -> None:
    ctx = MagicMock(spec=PipelineRunContext, selected_stages=[])

    step1 = MagicMock(spec=StepWrapper)
    step2 = MagicMock(spec=StepWrapper)
    step3 = MagicMock(spec=StepWrapper)

    # Use model_construct to skip validations
    stage = Stage.model_construct(steps=[step1, step2, step3])

    mock_runner = MagicMock(spec=StepRunner)
    mock_runner.run.side_effect = [None, 5, None]

    mock_factory = mocker.patch("pipeline_runner.runner.StepRunnerFactory")
    mock_factory.get.return_value = mock_runner

    runner = StageRunner(stage, ctx)
    exit_code = runner.run()

    assert exit_code == 5

    assert mock_factory.get.call_count == 2
    mock_factory.get.assert_any_call(step1, ctx)
    mock_factory.get.assert_any_call(step2, ctx)

    assert mock_runner.run.call_count == 2


def test_stage_runner_runs_only_specified_stages_if_selection_present(mocker: MockerFixture) -> None:
    ctx = MagicMock(spec=PipelineRunContext, selected_stages=["some stage", "another stage"])

    step1 = MagicMock(spec=StepWrapper)
    step2 = MagicMock(spec=StepWrapper)

    # Use model_construct to skip validations
    stage1 = Stage.model_construct(steps=[step1])
    stage1.name = "some stage"
    stage2 = Stage.model_construct(steps=[step2])
    stage2.name = "unselected stage"

    mock_runner = MagicMock(spec=StepRunner)
    mock_runner.run.return_value = 0

    mock_factory = mocker.patch("pipeline_runner.runner.StepRunnerFactory")
    mock_factory.get.return_value = mock_runner

    runner = StageRunner(stage1, ctx)
    exit_code = runner.run()
    assert exit_code == 0

    runner = StageRunner(stage2, ctx)
    exit_code = runner.run()
    assert exit_code == 0

    # Stage 2 should have been ignored
    mock_factory.get.assert_called_once_with(step1, ctx)
    mock_runner.run.assert_called_once()


def test_stage_runner_waits_for_input_on_manual_trigger(monkeypatch: MonkeyPatch, mocker: MockerFixture) -> None:
    ctx = MagicMock(spec=PipelineRunContext, selected_stages=[])

    step = MagicMock(spec=StepWrapper)

    # Use model_construct to skip validations
    stage = Stage.model_construct(steps=[step], trigger=Trigger.Manual)

    mock_runner = MagicMock(spec=StepRunner)
    mock_runner.run.return_value = 0

    mock_factory = mocker.patch("pipeline_runner.runner.StepRunnerFactory")
    mock_factory.get.return_value = mock_runner

    r, w = os.pipe()

    read_buffer = os.fdopen(r, "r")
    monkeypatch.setattr("sys.stdin", read_buffer)

    def _run_stage() -> int:
        runner = StageRunner(stage, ctx)
        return runner.run() or 0

    def _ensure_still_running(future_: Future[int], max_wait: int = 1) -> None:
        end = time.time() + max_wait
        while time.time() < end:
            time.sleep(0.01)
            assert not future_.done()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(_run_stage)

        _ensure_still_running(future)

        mock_factory.assert_not_called()

        with open(w, "w") as write_buffer:
            write_buffer.write("\n")

        res = future.result(timeout=1)

    assert res == 0
    mock_factory.get.assert_called_once_with(step, ctx)
