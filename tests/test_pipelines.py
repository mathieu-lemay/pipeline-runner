from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from pipeline_runner import PipelineRunner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cache_directory():
    with TemporaryDirectory() as tempdir:
        p = patch("pipeline_runner.utils.get_user_cache_directory")
        m = p.start()

        m.return_value = tempdir

        yield tempdir

        p.stop()


def test_success():
    runner = PipelineRunner("custom.test_success")
    result = runner.run()

    assert result.ok


def test_failure():
    runner = PipelineRunner("custom.test_failure")
    result = runner.run()

    assert result.ok is False
    assert result.exit_code == 69


def test_run_as_user():
    runner = PipelineRunner("custom.test_run_as_user")
    result = runner.run()

    assert result.ok
