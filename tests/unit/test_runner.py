import concurrent.futures
import os
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

from _pytest.monkeypatch import MonkeyPatch
from pytest_mock import MockerFixture

from pipeline_runner.context import PipelineRunContext
from pipeline_runner.models import Stage, StepWrapper, Trigger
from pipeline_runner.runner import StageRunner, StepRunner


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
