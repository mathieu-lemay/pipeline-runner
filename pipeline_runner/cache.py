import logging
import os.path
from tempfile import NamedTemporaryFile
from time import time as ts
from typing import Dict, List

from . import utils
from .config import config
from .container import ContainerRunner
from .models import Cache

logger = logging.getLogger(__name__)


class CacheNotFound(Exception):
    pass


class CacheManager:
    def __init__(self, container: ContainerRunner, cache_definitions: Dict[str, Cache]):
        self._container = container
        self._cache_definitions = cache_definitions

        self._ignored_caches = {"docker"}

    def upload(self, cache_names: List[str]):
        for cache in cache_names:
            if self._should_ignore(cache):
                logger.info("Cache '%s': Ignoring", cache)
                continue

            try:
                self._upload_cache(cache)
            except CacheNotFound:
                logger.info("Cache '%s': Not found: Skipping", cache)
            else:
                self._restore_cache(cache)

    def download(self, cache_names: List[str]):
        for cache in cache_names:
            if self._should_ignore(cache):
                logger.info("Cache '%s': Ignoring", cache)
                continue

            self._prepare_cache_for_download(cache)
            self._download_cache(cache)

    def _upload_cache(self, cache_name: str):
        if self._should_ignore(cache_name):
            logger.info("Cache '%s': Ignoring", cache_name)
            return

        local_cache_archive_path = self._get_local_cache_archive_path(cache_name)

        if not os.path.exists(local_cache_archive_path):
            raise CacheNotFound()

        remote_cache_directory = self._get_remote_temp_directory(cache_name)
        remote_cache_parent_directory = os.path.dirname(remote_cache_directory)

        cache_archive_size = os.path.getsize(local_cache_archive_path)

        logger.info("Cache '%s': Uploading", cache_name)

        t = ts()

        prepare_cache_dir_cmd = (
            f'[ -d "{remote_cache_directory}" ] && rm -rf "{remote_cache_directory}"; '
            f'mkdir -p "{remote_cache_parent_directory}"'
        )
        res, output = self._container.run_command(prepare_cache_dir_cmd)
        if res != 0:
            logger.error("Remote command failed: %s", output.decode())
            raise Exception(f"Error uploading cache: {cache_name}")

        with open(local_cache_archive_path, "rb") as f:
            success = self._container.put_archive(remote_cache_parent_directory, f)
            if not success:
                raise Exception(f"Error uploading cache: {cache_name}")

        t = ts() - t

        logger.info(
            "Cache '%s': Uploaded %s in %.3fs", cache_name, utils.get_human_readable_size(cache_archive_size), t
        )

    def _restore_cache(self, cache_name):
        restore_cache_script = []

        temp_dir = self._get_remote_temp_directory(cache_name)
        target_dir = self._cache_definitions[cache_name].path
        restore_cache_script.append(f'if [ -e "{target_dir}" ]; then rm -rf "{target_dir}"; fi')
        restore_cache_script.append(f'mkdir -p "$(dirname "{target_dir}")"')
        restore_cache_script.append(f'mv "{temp_dir}" "{target_dir}"')

        exit_code, output = self._container.run_command("\n".join(restore_cache_script))
        if exit_code != 0:
            raise Exception(f"Error restoring cache: {cache_name}: {output.decode()}")

    def _prepare_cache_for_download(self, cache_name):
        remote_dir = self._cache_definitions[cache_name].path
        target_dir = self._get_remote_temp_directory(cache_name)

        # TODO: Escape remote dir in a better way
        if remote_dir.startswith("~"):
            remote_dir = remote_dir.replace("~", "$HOME", 1)

        prepare_cache_cmd = f'if [ -e "{remote_dir}" ]; then mv "{remote_dir}" "{target_dir}"; fi'

        exit_code, output = self._container.run_command(prepare_cache_cmd)
        if exit_code != 0:
            raise Exception(f"Error preparing cache: {cache_name}: {output.decode()}")

    def _download_cache(self, cache_name: str):
        remote_cache_directory = self._get_remote_temp_directory(cache_name)

        if not self._container.path_exists(remote_cache_directory):
            logger.info("Cache '%s': Not found", cache_name)
            return

        logger.info("Cache '%s': Downloading", cache_name)

        t = ts()

        with NamedTemporaryFile(dir=utils.get_local_cache_directory(), delete=False) as f:
            try:
                data, _ = self._container.get_archive(remote_cache_directory)
                size = 0
                for chunk in data:
                    size += len(chunk)
                    f.write(chunk)
            except Exception as e:
                logger.error(f"Error getting cache from container: {cache_name}: {e}")
                os.unlink(f.name)
                return
            else:
                local_cache_archive_path = self._get_local_cache_archive_path(cache_name)
                os.rename(f.name, local_cache_archive_path)

        t = ts() - t

        logger.info("Cache '%s': Downloaded %s in %.3fs", cache_name, utils.get_human_readable_size(size), t)

    def _should_ignore(self, cache_name: str) -> bool:
        return cache_name in self._ignored_caches

    @staticmethod
    def _get_local_cache_archive_path(cache_name: str) -> str:
        return os.path.join(utils.get_local_cache_directory(), f"{cache_name}.tar")

    @staticmethod
    def _get_remote_temp_directory(cache_name: str) -> str:
        return os.path.join(config.caches_dir, cache_name)
