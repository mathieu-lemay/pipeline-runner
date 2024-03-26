import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from _pytest.logging import LogCaptureFixture
from faker import Faker
from pytest_mock import MockerFixture

from pipeline_runner.cache import CacheSave, get_local_cache_archive_path
from pipeline_runner.container import ContainerRunner


def test_cache_is_created_if_it_does_not_exist(tmp_path: Path, mocker: MockerFixture, faker: Faker) -> None:
    container = Mock(ContainerRunner)
    cache_name = faker.pystr()
    cache_definitions = {cache_name: faker.file_path(extension="")}

    local_archive_path = get_local_cache_archive_path(tmp_path.as_posix(), cache_name)

    prepare_mock = mocker.patch.object(CacheSave, "_prepare")
    download_mock = mocker.patch.object(CacheSave, "_download")

    saver = CacheSave(container, tmp_path.as_posix(), cache_definitions, cache_name)
    saver.save()

    prepare_mock.assert_called_once()
    download_mock.assert_called_once_with(prepare_mock.return_value, local_archive_path)


@pytest.mark.parametrize(
    ("delta", "should_update"),
    [
        (timedelta(), False),
        (timedelta(days=6, seconds=3600 * 23), False),
        (timedelta(days=7), True),
        (timedelta(days=14), True),
    ],
)
def test_cache_is_updated_if_it_is_older_than_a_week(
    tmp_path: Path,
    mocker: MockerFixture,
    caplog: LogCaptureFixture,
    faker: Faker,
    delta: timedelta,
    should_update: bool,
) -> None:
    container = Mock(ContainerRunner)
    cache_name = faker.pystr()
    cache_definitions = {cache_name: faker.file_path(extension="")}

    local_archive_path = get_local_cache_archive_path(tmp_path.as_posix(), cache_name)

    with open(local_archive_path, "a"):
        file_mtime = (datetime.now() - delta).timestamp()
        os.utime(local_archive_path, (file_mtime, file_mtime))

    prepare_mock = mocker.patch.object(CacheSave, "_prepare")
    download_mock = mocker.patch.object(CacheSave, "_download")

    saver = CacheSave(container, tmp_path.as_posix(), cache_definitions, cache_name)
    saver.save()

    if should_update:
        prepare_mock.assert_called_once()
        download_mock.assert_called_once_with(prepare_mock.return_value, local_archive_path)

        assert "You already have a '{cache_name}' cache" not in caplog.text
    else:
        prepare_mock.assert_not_called()
        download_mock.assert_not_called()

        assert f"You already have a '{cache_name}' cache" in caplog.text
