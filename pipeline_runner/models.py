import uuid
from typing import List, Optional, Union


def _generate_id():
    return str(uuid.uuid4())


class DebugMixin:
    def __repr__(self):
        values = [f"{k}: {repr(v)}" for k, v in self.__dict__.items() if k[0] != "_"]
        return f"{type(self).__name__} {{ {', '.join(values)} }}"

    def json(self):
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}


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
        run_as_user: Optional[int] = None,
        aws: Optional[dict] = None,
    ):
        self.name = name
        self.username = username
        self.password = password
        self.email = email
        self.run_as_user = run_as_user
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
        deployment: str,
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
        self.deployment = deployment

        self.uuid = _generate_id()


class ParallelStep(DebugMixin):
    def __init__(self, steps: List[Step]):
        self.steps = steps

        self.uuid = _generate_id()


class Pipeline(DebugMixin):
    def __init__(self, path: str, name: str, steps: [Union[Step, ParallelStep]], *_, **_kwargs):
        self.path = path
        self.name = name
        self.steps = steps

        self.uuid = _generate_id()
        self.number = 0


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


class PipelineInfo:
    def __init__(self, build_number: int = 0):
        self.build_number = build_number

    @classmethod
    def from_json(cls, json_: dict) -> "PipelineInfo":
        build_number = json_.get("build_number", 0)
        return cls(build_number=build_number)

    def to_json(self) -> dict:
        return {"build_number": self.build_number}


class PipelineResult:
    def __init__(self, exit_code: int):
        self.exit_code = exit_code

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
