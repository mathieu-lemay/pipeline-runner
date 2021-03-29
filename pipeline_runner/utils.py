import json
import os
import uuid
from typing import List, Optional, Union

from git import Repo
from xdg import xdg_cache_home, xdg_data_home

from .config import config

_git_repo = None


def _get_git_repo() -> Repo:
    global _git_repo

    if not _git_repo:
        _git_repo = Repo(config.project_directory)

    return _git_repo


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


def get_local_cache_directory() -> str:
    d = os.path.join(_get_project_cache_directory(), "caches")

    if not os.path.exists(d):
        os.makedirs(d)

    return d


def get_artifact_directory(pipeline_uuid: str) -> str:
    d = os.path.join(_get_project_cache_directory(), "artifacts", pipeline_uuid)

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


def wrap_in_shell(command: Union[str, List[str]], shell: Optional[str] = "bash", stop_on_error=True):
    command = stringify(command)

    wrapped = [shell]
    if stop_on_error:
        wrapped.append("-e")

    wrapped += ["-c", command]

    return wrapped


def generate_id():
    return str(uuid.uuid4())


def dumps(*args, **kwargs):
    def _handler(obj):
        if isinstance(obj, DebugMixin):
            return obj.json()
        else:
            return None

    return json.dumps(*args, default=_handler, **kwargs)


class DebugMixin:
    def __repr__(self):
        values = [f"{k}: {repr(v)}" for k, v in self.__dict__.items() if k[0] != "_"]
        return f"{type(self).__name__} {{ {', '.join(values)} }}"

    def json(self):
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}


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
