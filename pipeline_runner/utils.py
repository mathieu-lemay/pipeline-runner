import json
import logging
import os
import sys
from typing import List, Union

from git import Repo
from xdg import xdg_cache_home, xdg_data_home

from .config import config
from .models import DebugMixin, Pipeline, PipelineInfo

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
    d = _get_project_cache_directory()

    if not os.path.exists(d):
        os.makedirs(d)

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
    d = os.path.join(_get_project_cache_directory(), "caches")

    if not os.path.exists(d):
        os.makedirs(d)

    return d


def _get_pipeline_cache_directory(pipeline: Pipeline) -> str:
    return os.path.join(_get_project_cache_directory(), "pipelines", f"{pipeline.number}-{pipeline.uuid}")


def get_log_directory(pipeline: Pipeline) -> str:
    d = os.path.join(_get_pipeline_cache_directory(pipeline), "logs")

    if not os.path.exists(d):
        os.makedirs(d)

    return d


def get_artifact_directory(pipeline: Pipeline) -> str:
    d = os.path.join(_get_pipeline_cache_directory(pipeline), "artifacts")

    if not os.path.exists(d):
        os.makedirs(d)

    return d


def get_data_directory() -> str:
    return os.path.join(xdg_data_home(), "pipeline-runner")


def stringify(value: Union[str, List[str]], sep: str = " "):
    if isinstance(value, list):
        value = sep.join(value)

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
