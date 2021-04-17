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

DOCKER_IMAGES_ARCHIVE_FILE_NAME = "images.tar"


class CacheManager:
    def __init__(self, container: ContainerRunner, cache_definitions: Dict[str, Cache]):
        self._container = container
        self._cache_definitions = cache_definitions

        self._ignored_caches = {"docker"}

    def upload(self, cache_names: List[str]):
        for name in cache_names:
            cu = CacheUploadFactory.get(self._container, name, self._cache_definitions)
            cu.upload()

    def download(self, cache_names: List[str]):
        for name in cache_names:
            cd = CacheDownloadFactory.get(self._container, name, self._cache_definitions)
            cd.download()


class CacheUpload:
    def __init__(self, container: ContainerRunner, cache_name: str, cache_definitions: Dict[str, Cache]):
        self._container = container
        self._cache_name = cache_name
        self._cache_definitions = cache_definitions

    def upload(self):
        cache_file = self._get_local_cache_file()

        if not cache_file:
            logger.info("Cache '%s': Not found: Skipping", self._cache_name)
            return

        self._upload_cache(cache_file)
        self._restore_cache()

    def _get_local_cache_file(self):
        local_cache_archive_path = get_local_cache_archive_path(self._cache_name)
        if not os.path.exists(local_cache_archive_path):
            return None

        return local_cache_archive_path

    def _upload_cache(self, cache_file):
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

    def _restore_cache(self):
        temp_dir = get_remote_temp_directory(self._cache_name)
        target_dir = sanitize_remote_path(self._cache_definitions[self._cache_name].path)

        restore_cache_script = [
            f'if [ -e "{target_dir}" ]; then rm -rf "{target_dir}"; fi',
            f'mkdir -p "$(dirname "{target_dir}")"',
            f'mv "{temp_dir}" "{target_dir}"',
        ]

        exit_code, output = self._container.run_command("\n".join(restore_cache_script))
        if exit_code != 0:
            raise Exception(f"Error restoring cache: {self._cache_name}: {output.decode()}")


class DockerCacheUpload(CacheUpload):
    def _restore_cache(self):
        image_archive = os.path.join(config.caches_dir, DOCKER_IMAGES_ARCHIVE_FILE_NAME)

        restore_cache_script = [
            f'docker image load < "{image_archive}"',
            f'rm "{image_archive}"',
        ]

        exit_code, output = self._container.run_command("\n".join(restore_cache_script))
        if exit_code != 0:
            raise Exception(f"Error restoring cache: {self._cache_name}: {output.decode()}")


class CacheUploadFactory:
    @staticmethod
    def get(container: ContainerRunner, cache_name: str, cache_definitions: Dict[str, Cache]) -> CacheUpload:
        if cache_name == "docker":
            cls = DockerCacheUpload
        else:
            cls = CacheUpload

        return cls(container, cache_name, cache_definitions)


class CacheDownload:
    def __init__(self, container: ContainerRunner, cache_name: str, cache_definitions: Dict[str, Cache]):
        self._container = container
        self._cache_name = cache_name
        self._cache_definitions = cache_definitions

    def download(self):
        remote_cache_directory = self._prepare()

        local_cache_archive_path = get_local_cache_archive_path(self._cache_name)
        self._download(remote_cache_directory, local_cache_archive_path)

    def _prepare(self) -> str:
        remote_dir = sanitize_remote_path(self._cache_definitions[self._cache_name].path)
        target_dir = get_remote_temp_directory(self._cache_name)

        prepare_cache_cmd = f'if [ -e "{remote_dir}" ]; then mv "{remote_dir}" "{target_dir}"; fi'

        exit_code, output = self._container.run_command(prepare_cache_cmd)
        if exit_code != 0:
            raise Exception(f"Error preparing cache: {self._cache_name}: {output.decode()}")

        return target_dir

    def _download(self, src: str, dst: str):
        if not self._container.path_exists(src):
            logger.info("Cache '%s': Not found", self._cache_name)
            return

        logger.info("Cache '%s': Downloading", self._cache_name)

        t = ts()

        with NamedTemporaryFile(dir=utils.get_local_cache_directory(), delete=False) as f:
            try:
                logger.debug(f"Downloading cache folder '{src}' to '{f}'")
                data, _ = self._container.get_archive(src)
                size = 0
                for chunk in data:
                    size += len(chunk)
                    f.write(chunk)
            except Exception as e:
                logger.error(f"Error getting cache from container: {self._cache_name}: {e}")
                os.unlink(f.name)
                return
            else:
                logger.debug(f"Moving temp cache archive {f.name} to {dst}")
                os.rename(f.name, dst)

        t = ts() - t

        logger.info("Cache '%s': Downloaded %s in %.3fs", self._cache_name, utils.get_human_readable_size(size), t)


class DockerCacheDownload(CacheDownload):
    def _prepare(self):
        cache_dir = get_remote_temp_directory(self._cache_name)
        img_archive = os.path.join(cache_dir, DOCKER_IMAGES_ARCHIVE_FILE_NAME)

        prepare_cache_cmd = [
            "image_ids=$(docker image ls -a -q)",
            'image_repos=$(docker image ls --format "{{.Repository}}" | sort -u | grep -v "<none>")',
            'images="${image_ids} ${image_repos}"',
            'if [ -z "${images}" ]; then exit 0; fi',
            f'mkdir -p "{cache_dir}"',
            f"docker image save ${{images}} -o {img_archive}",  # No quotes around ${images} as we want it expanded
        ]

        exit_code, output = self._container.run_command("\n".join(prepare_cache_cmd))
        if exit_code != 0:
            raise Exception(f"Error preparing cache: {self._cache_name}: {output.decode()}")

        return img_archive


class CacheDownloadFactory:
    @staticmethod
    def get(container: ContainerRunner, cache_name: str, cache_definitions: Dict[str, Cache]) -> CacheDownload:
        if cache_name == "docker":
            cls = DockerCacheDownload
        else:
            cls = CacheDownload

        return cls(container, cache_name, cache_definitions)


def get_local_cache_archive_path(cache_name: str) -> str:
    return os.path.join(utils.get_local_cache_directory(), f"{cache_name}.tar")


def get_remote_temp_directory(cache_name: str) -> str:
    return os.path.join(config.caches_dir, cache_name)


def sanitize_remote_path(path: str) -> str:
    if path.startswith("~"):
        path = path.replace("~", "$HOME", 1)

    return path
