from tempfile import TemporaryDirectory

import pytest


@pytest.fixture(autouse=True)
def user_cache_directory(mocker):
    with TemporaryDirectory() as cache_dir:
        m = mocker.patch("pipeline_runner.utils.get_cache_directory")

        m.return_value = cache_dir

        yield cache_dir


@pytest.fixture(autouse=True)
def user_data_directory(mocker):
    with TemporaryDirectory() as data_dir:
        m = mocker.patch("pipeline_runner.utils.get_data_directory")

        m.return_value = data_dir

        yield data_dir
