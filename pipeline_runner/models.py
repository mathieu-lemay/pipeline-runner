from typing import List, Optional, Union

from .utils import DebugMixin, generate_id


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
    def __init__(
        self,
        name: str,
        image: Image = None,
        environment: {str: str} = None,
        memory: int = None,
        command: Union[str, List[str]] = None,
    ):
        self.name = name
        self.image = image
        self.environment = environment
        self.memory = memory
        self.command = command

    def update(self, service: "Service"):
        for attr in ("name", "image", "environment", "memory"):
            val = getattr(service, attr)
            if val is not None:
                setattr(self, attr, val)


class CloneSettings(DebugMixin):
    def __init__(
        self,
        depth: Optional[int] = None,
        lfs: Optional[bool] = None,
        enabled: Optional[bool] = None,
    ):
        self.depth = depth
        self.lfs = lfs
        self.enabled = enabled

    @classmethod
    def default(cls):
        return cls(depth=50, lfs=False, enabled=True)


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
        clone_settings: CloneSettings,
    ):
        self.name = name
        self.script = script
        self.image = image
        self.caches = caches or []
        self.services = services or []
        self.artifacts = artifacts or []
        self.after_script = after_script or []
        self.size = size
        self.clone_settings = clone_settings or CloneSettings()

        self.uuid = generate_id()


class ParallelStep(DebugMixin):
    def __init__(self, steps: List[Step]):
        self.steps = steps

        self.uuid = generate_id()


class Pipeline(DebugMixin):
    def __init__(self, path: str, name: str, steps: [Union[Step, ParallelStep]], *_, **_kwargs):
        self.path = path
        self.name = name
        self.steps = steps

        self.uuid = generate_id()


class Pipelines(DebugMixin):
    def __init__(
        self,
        image=None,
        pipelines: [Pipeline] = None,
        caches: [Cache] = None,
        services: [Service] = None,
        clone_settings: CloneSettings = None,
    ):
        self.image = image
        self.pipelines = pipelines
        self.caches = caches
        self.services = services
        self.clone_settings = clone_settings or CloneSettings()

    def get_pipeline(self, path) -> Optional[Pipeline]:
        return self.pipelines.get(path)

    def get_available_pipelines(self) -> List[str]:
        return self.pipelines.keys()
