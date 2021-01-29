import json

DEFAULT_IMAGE = "atlassian/default-image:latest"

DEFAULT_CACHES = {
    "composer": "~/.composer/cache",
    "dotnetcore": "~/.nuget/packages",
    "gradle": "~/.gradle/caches ",
    "ivy2": "~/.ivy2/cache",
    "maven": "~/.m2/repository",
    "node": "node_modules",
    "pip": "~/.cache/pip",
    "sbt": "~/.sbt",
}


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
