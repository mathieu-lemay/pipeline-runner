import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from _pytest.logging import LogCaptureFixture
from faker import Faker
from pytest_mock import MockerFixture

from pipeline_runner.cache import CacheSave, compute_cache_key, get_local_cache_archive_path
from pipeline_runner.container import ContainerRunner
from pipeline_runner.errors import InvalidCacheKeyError
from pipeline_runner.models import Cache, CacheKey, Repository


def test_cache_is_created_if_it_does_not_exist(tmp_path: Path, mocker: MockerFixture, faker: Faker) -> None:
    container = Mock(ContainerRunner)
    repository = Mock(Repository)
    cache_name = faker.pystr()
    cache_definitions = {cache_name: faker.file_path(extension="")}

    local_archive_path = get_local_cache_archive_path(tmp_path.as_posix(), cache_name)

    prepare_mock = mocker.patch.object(CacheSave, "_prepare")
    download_mock = mocker.patch.object(CacheSave, "_download")

    saver = CacheSave(container, repository, tmp_path.as_posix(), cache_definitions, cache_name)
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
    repository = Mock(Repository)
    cache_name = faker.pystr()
    cache_definitions = {cache_name: faker.file_path(extension="")}

    local_archive_path = get_local_cache_archive_path(tmp_path.as_posix(), cache_name)

    with open(local_archive_path, "a"):
        file_mtime = (datetime.now() - delta).timestamp()
        os.utime(local_archive_path, (file_mtime, file_mtime))

    prepare_mock = mocker.patch.object(CacheSave, "_prepare")
    download_mock = mocker.patch.object(CacheSave, "_download")

    saver = CacheSave(container, repository, tmp_path.as_posix(), cache_definitions, cache_name)
    saver.save()

    if should_update:
        prepare_mock.assert_called_once()
        download_mock.assert_called_once_with(prepare_mock.return_value, local_archive_path)

        assert "You already have a '{cache_name}' cache" not in caplog.text
    else:
        prepare_mock.assert_not_called()
        download_mock.assert_not_called()

        assert f"You already have a '{cache_name}' cache" in caplog.text


def test_compute_cache_key_for_regular_cache(faker: Faker) -> None:
    repository = Mock(Repository)
    cache_name = faker.pystr()
    cache = faker.pystr()

    assert compute_cache_key(cache_name, cache, repository) == cache_name


def test_compute_cache_key_for_custom_cache(tmp_path: Path, faker: Faker) -> None:
    repository = Mock(Repository, path=tmp_path.as_posix())
    cache_name = faker.pystr()

    file1_content = faker.pystr()
    file1 = tmp_path / faker.pystr()
    file1.write_text(file1_content)

    file2_content = faker.pystr()
    file2 = tmp_path / faker.pystr()
    file2.write_text(file2_content)

    cache = Cache(
        key=CacheKey(files=[file1.as_posix(), file2.as_posix()]),
        path=faker.file_path(extension=""),
    )

    hasher = hashlib.sha256()
    hasher.update(file1_content.encode("utf-8"))
    hasher.update(file2_content.encode("utf-8"))
    expected_hash = hasher.hexdigest()

    assert compute_cache_key(cache_name, cache, repository) == f"{cache_name}-{expected_hash}"


def test_compute_cache_key_for_custom_cache_is_computed_only_once(tmp_path: Path, faker: Faker) -> None:
    repository = Mock(Repository, path=tmp_path.as_posix())
    cache_name = faker.pystr()

    file1_content = faker.pystr()
    file1 = tmp_path / faker.pystr()
    file1.write_text(file1_content)

    cache = Cache(
        key=CacheKey(files=[file1.as_posix()]),
        path=faker.file_path(extension=""),
    )

    key = compute_cache_key(cache_name, cache, repository)

    file1.unlink()

    # Should use the pre-computed value and ignore the fact that the file was deleted
    assert compute_cache_key(cache_name, cache, repository) == key


def test_compute_cache_key_for_custom_cache_ignores_non_existing_files(tmp_path: Path, faker: Faker) -> None:
    repository = Mock(Repository, path=tmp_path.as_posix())
    cache_name = faker.pystr()

    file_content = faker.pystr()
    file = tmp_path / faker.pystr()
    file.write_text(file_content)

    cache = Cache(
        key=CacheKey(files=[faker.pystr(), file.as_posix(), faker.pystr()]),
        path=faker.file_path(extension=""),
    )

    hasher = hashlib.sha256()
    hasher.update(file_content.encode("utf-8"))
    expected_hash = hasher.hexdigest()

    assert compute_cache_key(cache_name, cache, repository) == f"{cache_name}-{expected_hash}"


def test_compute_cache_key_for_custom_cache_fails_if_all_files_are_invalid(tmp_path: Path, faker: Faker) -> None:
    repository = Mock(Repository, path=tmp_path.as_posix())
    cache_name = faker.pystr()

    cache = Cache(
        key=CacheKey(files=[faker.pystr()]),
        path=faker.file_path(extension=""),
    )

    with pytest.raises(InvalidCacheKeyError, match=f'Cache "{cache_name}": Cache key files could not be found'):
        compute_cache_key(cache_name, cache, repository)
