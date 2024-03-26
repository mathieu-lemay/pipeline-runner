import glob
import hashlib
import logging
import os.path
from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import lru_cache
from tempfile import NamedTemporaryFile
from time import time as ts

from . import utils
from .config import config
from .container import ContainerRunner
from .errors import InvalidCacheKeyError
from .models import Cache, CacheType, Repository

logger = logging.getLogger(__name__)

DOCKER_IMAGES_ARCHIVE_FILE_NAME = "images.tar"
CACHE_TTL = timedelta(days=7)


class CacheManager:
    def __init__(
        self,
        container: ContainerRunner,
        repository: Repository,
        local_cache_directory: str,
        cache_definitions: Mapping[str, CacheType],
    ) -> None:
        self._container = container
        self._repository = repository
        self._local_cache_directory = local_cache_directory
        self._cache_definitions = cache_definitions

        self._ignored_caches = {"docker"}

    def upload(self, cache_names: list[str]) -> None:
        for name in cache_names:
            cu = CacheRestoreFactory.get(
                self._container, self._repository, self._local_cache_directory, self._cache_definitions, name
            )
            cu.restore()

    def download(self, cache_names: list[str]) -> None:
        for name in cache_names:
            cd = CacheSaveFactory.get(
                self._container, self._repository, self._local_cache_directory, self._cache_definitions, name
            )
            cd.save()


class CacheRestore:
    def __init__(
        self,
        container: ContainerRunner,
        repository: Repository,
        cache_directory: str,
        cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> None:
        cache_definition = cache_definitions[cache_name]

        self._container = container
        self._repository = repository
        self._cache_directory = cache_directory
        self._cache_definition = cache_definition
        self._cache_name = cache_name
        self._cache_key = compute_cache_key(cache_name, cache_definition, repository)

    def restore(self) -> None:
        cache_file = self._get_local_cache_file()

        if not cache_file:
            logger.info("Cache '%s': Not found: Skipping", self._cache_name)
            return

        self._upload_cache(cache_file)
        self._restore_cache()

    def _get_local_cache_file(self) -> str | None:
        local_cache_archive_path = get_local_cache_archive_path(self._cache_directory, self._cache_key)
        if not os.path.exists(local_cache_archive_path):
            return None

        return local_cache_archive_path

    def _upload_cache(self, cache_file: str) -> None:
        remote_cache_directory = get_remote_temp_directory(self._cache_name)
        remote_cache_parent_directory = os.path.dirname(remote_cache_directory)

        cache_archive_size = os.path.getsize(cache_file)

        logger.info("Cache '%s': Uploading", self._cache_name)

        t = ts()

        prepare_cache_dir_cmd = (
            f'[ -d "{remote_cache_directory}" ] && rm -rf "{remote_cache_directory}"; '
            f'mkdir -p "{remote_cache_parent_directory}"'
        )
        res, output = self._container.run_command(prepare_cache_dir_cmd)
        if res != 0:
            logger.error("Remote command failed: %s", output.decode())
            raise Exception(f"Error uploading cache: {self._cache_name}")

        with open(cache_file, "rb") as f:
            success = self._container.put_archive(remote_cache_parent_directory, f)
            if not success:
                raise Exception(f"Error uploading cache: {self._cache_name}")

        t = ts() - t

        logger.info(
            "Cache '%s': Uploaded %s in %.3fs", self._cache_name, utils.get_human_readable_size(cache_archive_size), t
        )

    def _restore_cache(self) -> None:
        temp_dir = get_remote_temp_directory(self._cache_name)
        target_dir = sanitize_remote_path(self._cache_definition)

        logger.info("Cache '%s': Restoring", self._cache_name)

        t = ts()

        restore_cache_script = [
            f'if [ -e "{target_dir}" ]; then rm -rf "{target_dir}"; fi',
            f'mkdir -p "$(dirname "{target_dir}")"',
            f'mv "{temp_dir}" "{target_dir}"',
        ]

        exit_code, output = self._container.run_command("\n".join(restore_cache_script))
        if exit_code != 0:
            raise Exception(f"Error restoring cache: {self._cache_name}: {output.decode()}")

        t = ts() - t

        logger.info("Cache '%s': Restored in %.3fs", self._cache_name, t)


class NullCacheRestore(CacheRestore):
    def __init__(
        self,
        _container: ContainerRunner,
        _repository: Repository,
        _local_cache_directory: str,
        _cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> None:
        self._cache_name = cache_name

    def restore(self) -> None:
        logger.info("Cache '%s': Ignoring", self._cache_name)


class CacheRestoreFactory:
    @staticmethod
    def get(
        container: ContainerRunner,
        repository: Repository,
        cache_directory: str,
        cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> CacheRestore:
        cls: type[CacheRestore | NullCacheRestore]

        cls = NullCacheRestore if cache_name == "docker" else CacheRestore

        return cls(container, repository, cache_directory, cache_definitions, cache_name)


class CacheSave:
    def __init__(
        self,
        container: ContainerRunner,
        repository: Repository,
        local_cache_directory: str,
        cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> None:
        cache_definition = cache_definitions[cache_name]
        self._container = container
        self._repository = repository
        self._local_cache_directory = local_cache_directory
        self._cache_definition = cache_definition
        self._cache_name = cache_name
        self._cache_key = compute_cache_key(cache_name, cache_definition, repository)

    def save(self) -> None:
        local_cache_archive_path = get_local_cache_archive_path(self._local_cache_directory, self._cache_key)

        if not self._cache_should_be_updated(local_cache_archive_path):
            logger.info("You already have a '%s' cache so we won't create it again", self._cache_name)
            return

        remote_cache_directory = self._prepare()
        self._download(remote_cache_directory, local_cache_archive_path)

    @staticmethod
    def _cache_should_be_updated(local_cache_archive_path: str) -> bool:
        if not os.path.exists(local_cache_archive_path):
            return True

        mtime = os.path.getmtime(local_cache_archive_path)
        return mtime < (datetime.now() - CACHE_TTL).timestamp()

    def _prepare(self) -> str:
        remote_dir = sanitize_remote_path(self._cache_definition)
        target_dir = get_remote_temp_directory(self._cache_name)

        logger.info("Cache '%s': Preparing", self._cache_name)

        t = ts()

        prepare_cache_cmd = f'if [ -e "{remote_dir}" ]; then mv "{remote_dir}" "{target_dir}"; fi'

        exit_code, output = self._container.run_command(prepare_cache_cmd)
        if exit_code != 0:
            raise Exception(f"Error preparing cache: {self._cache_name}: {output.decode()}")

        t = ts() - t

        logger.info("Cache '%s': Prepared in %.3fs", self._cache_name, t)

        return target_dir

    def _download(self, src: str, dst: str) -> None:
        if not self._container.path_exists(src):
            logger.info("Cache '%s': Not found", self._cache_name)
            return

        logger.info("Cache '%s': Downloading", self._cache_name)

        t = ts()

        with NamedTemporaryFile(dir=self._local_cache_directory, delete=False) as f:
            try:
                logger.debug("Downloading cache folder '%s' to '%s'", src, f.name)
                data, _ = self._container.get_archive(src)
                size = 0
                for chunk in data:
                    size += len(chunk)
                    f.write(chunk)
            except Exception:
                logger.exception("Error getting cache from container: %s", self._cache_name)
                os.unlink(f.name)
                return
            else:
                logger.debug("Moving temp cache archive %s to %s", f.name, dst)
                os.rename(f.name, dst)

        t = ts() - t

        logger.info("Cache '%s': Downloaded %s in %.3fs", self._cache_name, utils.get_human_readable_size(size), t)


class NullCacheSave(CacheSave):
    def __init__(
        self,
        _container: ContainerRunner,
        _repository: Repository,
        _local_cache_directory: str,
        _cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> None:
        self._cache_name = cache_name

    def save(self) -> None:
        logger.info("Cache '%s': Ignoring", self._cache_name)


class CacheSaveFactory:
    @staticmethod
    def get(
        container: ContainerRunner,
        repository: Repository,
        local_cache_directory: str,
        cache_definitions: Mapping[str, CacheType],
        cache_name: str,
    ) -> CacheSave:
        cls: type[CacheSave | NullCacheSave]

        cls = NullCacheSave if cache_name == "docker" else CacheSave

        return cls(container, repository, local_cache_directory, cache_definitions, cache_name)


def get_local_cache_archive_path(cache_directory: str, cache_name: str) -> str:
    return os.path.join(cache_directory, f"{cache_name}.tar")


def get_remote_temp_directory(cache_name: str) -> str:
    return os.path.join(config.caches_dir, cache_name)


def sanitize_remote_path(cache: CacheType) -> str:
    match cache:
        case str():
            path = cache
        case Cache():
            path = cache.path

    if path.startswith("~"):
        path = path.replace("~", "$HOME", 1)

    return path


@lru_cache
def compute_cache_key(cache_name: str, cache: CacheType, repository: Repository) -> str:
    if isinstance(cache, str):
        return cache_name

    is_valid = False
    hasher = hashlib.sha256()

    for key_file in cache.key.files:
        for fp in glob.glob(os.path.join(repository.path, key_file), recursive=True):
            if not os.path.isfile(fp):
                continue

            is_valid = True

            with open(fp, "rb") as f:
                hasher.update(f.read())

    if not is_valid:
        raise InvalidCacheKeyError(cache_name)

    return f"{cache_name}-{hasher.hexdigest()}"
