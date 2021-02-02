from typing import List, Optional, Union

from .utils import DebugMixin


class Cache(DebugMixin):
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path


class Image(DebugMixin):
    def __init__(
        self,
        name: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        email: Optional[str] = None,
        user: Optional[str] = None,
        aws: Optional[dict] = None,
    ):
        self.name = name
        self.username = username
        self.password = password
        self.email = email
        self.aws = aws


class Service(DebugMixin):
    def __init__(self, name: str, image: Image = None, environment: {str: str} = None, memory: int = None):
        self.name = name
        self.image = image
        self.environment = environment
        self.memory = memory

    def update(self, service: "Service"):
        for attr in ("name", "image", "environment", "memory"):
            val = getattr(service, attr)
            if val is not None:
                setattr(self, attr, val)


class Step(DebugMixin):
    def __init__(
        self,
        name: str,
        script: [str],
        image: Optional[Image],
        caches: Optional[List[str]],
        services: Optional[List[str]],
        artifacts: Optional[List[str]],
        after_script: Optional[List[str]],
        size: int,
    ):
        self.name = name
        self.script = script
        self.image = image
        self.caches = caches or []
        self.services = services or []
        self.artifacts = artifacts or []
        self.after_script = after_script or []
        self.size = size


class ParallelStep(DebugMixin):
    def __init__(self, steps: List[Step]):
        self.steps = steps


class Pipeline(DebugMixin):
    def __init__(self, path: str, name: str, steps: [Union[Step, ParallelStep]], *_, **_kwargs):
        self.path = path
        self.name = name
        self.steps = steps


class Pipelines(DebugMixin):
    def __init__(
        self,
        image=None,
        pipelines: [Pipeline] = None,
        caches: [Cache] = None,
        services: [Service] = None,
    ):
        self.image = image
        self.pipelines = pipelines
        self.caches = caches
        self.services = services

    def get_pipeline(self, path) -> Optional[Pipeline]:
        return self.pipelines.get(path)

    def get_available_pipelines(self) -> List[str]:
        return self.pipelines.keys()
