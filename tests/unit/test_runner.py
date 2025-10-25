from logging import Logger
from pathlib import Path
from typing import cast

import pytest
from click import UsageError
from docker import DockerClient  # type: ignore[import-untyped]
from faker.proxy import Faker
from pytest_mock import MockerFixture

from pipeline_runner.runner import StepRunner


@pytest.fixture(autouse=True)
def docker_client(mocker: MockerFixture) -> DockerClient:
    mock_client = mocker.Mock()
    mocker.patch("pipeline_runner.runner.docker.from_env", return_value=mock_client)
    return mock_client


@pytest.fixture(autouse=True)
def output_logger(mocker: MockerFixture) -> Logger:
    mock_logger = mocker.Mock()
    mocker.patch("pipeline_runner.runner.utils.get_output_logger", return_value=mock_logger)
    return cast("Logger", mock_logger)


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
