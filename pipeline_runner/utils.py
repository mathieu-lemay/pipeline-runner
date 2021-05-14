import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Union

import bs4
import requests
from git import Repo
from xdg import xdg_cache_home, xdg_data_home

from .config import config
from .models import DebugMixin, Pipeline, PipelineInfo

logger = logging.getLogger(__name__)

_git_repo = None


def _get_git_repo() -> Repo:
    global _git_repo

    if not _git_repo:
        _git_repo = Repo(config.project_directory)

    return _git_repo


def get_output_logger(pipeline: Pipeline, name: str) -> logging.Logger:
    formatter = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.terminator = ""

    file_handler = logging.FileHandler(os.path.join(get_log_directory(pipeline), f"{name}.txt"))
    file_handler.setFormatter(formatter)
    file_handler.terminator = ""

    output_logger = logging.getLogger(f"pipeline_runner_output.{name}")
    output_logger.handlers.append(stream_handler)
    output_logger.handlers.append(file_handler)
    output_logger.setLevel("DEBUG")

    return output_logger


def get_git_current_branch() -> str:
    r = _get_git_repo()
    return r.active_branch.name


def get_git_current_commit() -> str:
    r = _get_git_repo()
    return r.head.commit.hexsha


def get_user_cache_directory() -> str:
    return os.path.join(xdg_cache_home(), "pipeline-runner")


def _get_project_cache_directory() -> str:
    return os.path.join(get_user_cache_directory(), config.project_env_name)


def get_project_pipelines_info_file() -> str:
    d = _ensure_directory(_get_project_cache_directory())

    return os.path.join(d, "info.json")


def load_project_pipelines_info() -> PipelineInfo:
    fp = get_project_pipelines_info_file()
    if not os.path.exists(fp):
        return PipelineInfo()

    with open(fp) as f:
        return PipelineInfo.from_json(json.load(f))


def save_project_pipelines_info(pi: PipelineInfo):
    fp = get_project_pipelines_info_file()

    with open(fp, "w") as f:
        json.dump(pi.to_json(), f)


def get_local_cache_directory() -> str:
    return _ensure_directory(os.path.join(_get_project_cache_directory(), "caches"))


def _get_pipeline_cache_directory(pipeline: Pipeline) -> str:
    return os.path.join(_get_project_cache_directory(), "pipelines", f"{pipeline.number}-{pipeline.uuid}")


def get_log_directory(pipeline: Pipeline) -> str:
    return _ensure_directory(os.path.join(_get_pipeline_cache_directory(pipeline), "logs"))


def get_artifact_directory(pipeline: Pipeline) -> str:
    return _ensure_directory(os.path.join(_get_pipeline_cache_directory(pipeline), "artifacts"))


def get_data_directory() -> str:
    return _ensure_directory(os.path.join(xdg_data_home(), "pipeline-runner"))


def _ensure_directory(path) -> str:
    if not os.path.exists(path):
        os.makedirs(path)

    return path


def stringify(value: Union[str, List[str]], sep: str = " "):
    if isinstance(value, list):
        value = sep.join(value)

    return value


def escape_shell_string(value: str) -> str:
    for c in "\\$%{}\"'":
        value = value.replace(c, fr"\x{ord(c):02x}")

    return value


def get_human_readable_size(num):
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB"]:

        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"

        num /= 1024.0

    return f"{num:.1f}{unit}"


def wrap_in_shell(command: Union[str, List[str]], stop_on_error=True):
    command = stringify(command)

    wrapped = ["sh"]
    if stop_on_error:
        wrapped.append("-e")

    wrapped += ["-c", command]

    return wrapped


def dumps(*args, **kwargs):
    def _handler(obj):
        if isinstance(obj, DebugMixin):
            return obj.json()
        else:
            return None

    return json.dumps(*args, default=_handler, **kwargs)


def get_docker_binary() -> str:
    docker_binary_path = os.path.join(get_user_cache_directory(), "docker.bin")

    dbu = DockerBinaryDownloader(docker_binary_path)
    dbu.download()

    return docker_binary_path


class DockerBinaryDownloader:
    def __init__(self, file_path: str):
        self._file_path = file_path
        self._latest_version: Optional[str] = None

    def download(self):
        if os.path.exists(self._file_path) and not self._file_needs_refresh():
            logger.debug("Docker binary is present and up-to-date")
            return

        self._download_latest_version()

    def _download_latest_version(self):
        version = self._get_latest_version()
        url = f"https://download.docker.com/linux/static/stable/x86_64/docker-{version}.tgz"
        logger.info("Downloading docker binary from: %s", url)

        resp = requests.get(url, stream=True)
        if not resp.ok:
            raise Exception(f"Error downloading docker binary: {resp.text}")

        with tempfile.TemporaryDirectory() as tempdir:
            with tarfile.open(fileobj=resp.raw, mode="r:*") as tar:
                tar.extractall(tempdir)

            shutil.move(os.path.join(tempdir, "docker", "docker"), self._file_path)

    def _file_needs_refresh(self) -> bool:
        stat = os.stat(self._file_path)
        if stat.st_mtime > (datetime.now() - timedelta(days=7)).timestamp():
            return False

        is_outdated = self._get_current_version() < self._get_latest_version()
        if not is_outdated:
            Path(self._file_path).touch()

        return is_outdated

    def _get_current_version(self) -> str:
        p = subprocess.run([self._file_path, "--version"], capture_output=True)
        version = re.match(r"^Docker version ([0-9a-z.]+),.*", p.stdout.decode())
        return version.group(1)

    def _get_latest_version(self) -> str:
        if self._latest_version:
            return self._latest_version

        resp = requests.get("https://download.docker.com/linux/static/stable/x86_64/")
        if not resp.ok:
            raise Exception(f"Unable to find docker's latest version: {resp.text}")

        doc = bs4.BeautifulSoup(resp.text, features="html.parser")
        pattern = re.compile("docker-([a-z0-9.]+).tgz")

        version = max(m.group(1) for m in map(lambda a: pattern.match(a.text), doc.find_all("a")) if m)
        self._latest_version = version

        return version


class FileStreamer:
    def __init__(self, it):
        self._it = it
        self._chunk = b""
        self._has_more_data = True

    def _grow_chunk(self):
        self._chunk = self._chunk + next(self._it)

    def read(self, n):
        if not self._has_more_data:
            return None

        try:
            while len(self._chunk) < n:
                self._grow_chunk()
            rv = self._chunk[:n]
            self._chunk = self._chunk[n:]
            return rv
        except StopIteration:
            rv = self._chunk
            self._has_more_data = False
            return rv
