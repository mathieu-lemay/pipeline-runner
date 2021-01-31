import json
import os
from typing import List, Union

from git import Repo
from xdg import xdg_cache_home

from . import config

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


def _get_user_cache_directory() -> str:
    return os.path.join(xdg_cache_home(), "pipeline-runner")


def _get_project_cache_directory() -> str:
    return os.path.join(_get_user_cache_directory(), config.project_env_name)


def get_artifact_directory(pipeline_uuid: str) -> str:
    d = os.path.join(_get_project_cache_directory(), "artifacts", pipeline_uuid)

    if not os.path.exists(d):
        os.makedirs(d)

    return d


def stringify(value: Union[str, List[str]], sep: str = " "):
    if isinstance(value, list):
        value = sep.join(value)

    return value


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
