import json
from typing import List, Union

from git import Repo

_git_repo = None


def _get_git_repo() -> Repo:
    global _git_repo

    if not _git_repo:
        from . import conf

        _git_repo = Repo(conf.project_directory)

    return _git_repo


def get_git_current_branch() -> str:
    r = _get_git_repo()
    return r.active_branch.name


def get_git_current_commit() -> str:
    r = _get_git_repo()
    return r.head.commit.hexsha


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
