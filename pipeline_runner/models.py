import os
from enum import Enum
from string import Template
from typing import Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Extra, Field, conlist, root_validator, validator


class BaseModel(PydanticBaseModel):
    class Config:
        extra = Extra.forbid
        allow_population_by_field_name = True


# noinspection PyMethodParameters
class AwsCredentials(BaseModel):
    access_key_id: str = Field(None, alias="access-key")
    secret_access_key: str = Field(None, alias="secret-key")
    oidc_role: str = Field(None, alias="oidc-role")

    @validator("oidc_role")
    def oidc_role_not_supported(cls, v):
        if v is not None:
            raise ValueError("aws oidc-role not supported")

        return v

    @root_validator
    def expand_env_vars(cls, values):
        for key, val in values.items():
            if isinstance(val, str):
                try:
                    values[key] = Template(val).substitute(os.environ)
                except KeyError as e:
                    raise ValueError(f"environment variable not defined: {e}")

        return values


# noinspection PyMethodParameters
class Image(BaseModel):
    name: str
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    run_as_user: Optional[int] = Field(None, alias="run-as-user")
    aws: Optional[AwsCredentials] = None

    @root_validator
    def expand_env_vars(cls, values):
        for key, val in values.items():
            if isinstance(val, str):
                try:
                    values[key] = Template(val).substitute(os.environ)
                except KeyError as e:
                    raise ValueError(f"environment variable not defined: {e}")

        return values


ImageType = Optional[Union[str, Image]]


class Service(BaseModel):
    image: ImageType = None
    variables: Dict[str, str] = Field(default_factory=dict)
    memory: Optional[int] = 1024

    # noinspection PyMethodParameters
    @validator("image")
    def convert_str_image_to_object(cls, value):
        if isinstance(value, str):
            return Image(name=value)

        return value


class Definitions(BaseModel):
    caches: Dict[str, str] = Field(default_factory=dict)
    services: Dict[str, Service] = Field(default_factory=dict)


class CloneSettings(BaseModel):
    depth: Optional[int] = 50
    lfs: Optional[bool] = False
    enabled: Optional[bool] = True

    @classmethod
    def empty(cls):
        return CloneSettings(depth=None, lfs=None, enabled=None)


class Trigger(str, Enum):
    Automatic = "automatic"
    Manual = "manual"


class StepSize(str, Enum):
    Simple = "1x"
    Double = "2x"

    def as_int(self) -> int:
        return {self.Simple: 1, self.Double: 2}[self]


class Step(BaseModel):
    name: Optional[str] = "<unnamed>"
    script: List[str]
    image: ImageType = None
    caches: Optional[List[str]] = Field(default_factory=list)
    services: Optional[List[str]] = Field(default_factory=list)
    artifacts: Optional[List[str]] = Field(default_factory=list)
    after_script: Optional[List[str]] = Field(default_factory=list, alias="after-script")
    size: Optional[StepSize] = StepSize.Simple
    clone_settings: Optional[CloneSettings] = Field(default_factory=CloneSettings.empty)
    deployment: Optional[str] = None
    trigger: Trigger = Trigger.Automatic

    # noinspection PyMethodParameters
    @validator("image")
    def convert_str_image_to_object(cls, value):
        if isinstance(value, str):
            return Image(name=value)

        return value


# noinspection PyUnresolvedReferences
class WrapperModel(BaseModel):
    wrapped: BaseModel

    def __getattr__(self, item):
        if item in self.__dict__:
            return self.__dict__[item]
        else:
            return getattr(self.wrapped, item)

    def __iter__(self):
        return iter(self.wrapped)

    def __getitem__(self, item):
        return self.wrapped[item]


class StepWrapper(WrapperModel):
    wrapped: Step = Field(alias="step")


class ParallelStep(WrapperModel):
    wrapped: conlist(StepWrapper, min_items=2) = Field(alias="parallel")


class Variable(BaseModel):
    name: str


class Variables(WrapperModel):
    wrapped: List[Variable] = Field(alias="variables")


class Pipeline(BaseModel):
    __root__: conlist(Union[StepWrapper, ParallelStep, Variables], min_items=1)

    # noinspection PyMethodParameters
    @validator("__root__")
    def validate_variables_must_be_first_element_of_list_if_present(cls, pipeline_items):
        if any(i for i in pipeline_items[1:] if isinstance(i, Variables)):
            raise ValueError("'variables' can only be the first element of the list")

        return pipeline_items

    def get_variables(self) -> Optional[Variables]:
        if isinstance(self.__root__[0], Variables):
            return self.__root__[0]

        return Variables(variables=[])

    def get_steps(self) -> List[Union[StepWrapper, ParallelStep]]:
        return [i for i in self.__root__ if not isinstance(i, Variables)]

    def __iter__(self):
        return iter(self.__root__)

    def __getitem__(self, item):
        return self.__root__[item]


class Pipelines(BaseModel):
    default: Optional[Pipeline] = None
    branches: Optional[Dict[str, Pipeline]] = Field(default_factory=list)
    pull_requests: Optional[Dict[str, Pipeline]] = Field(default_factory=list, alias="pull-requests")
    custom: Optional[Dict[str, Pipeline]] = Field(default_factory=list)

    def get_all(self) -> Dict[str, Pipeline]:
        pipelines = {}
        for attr in self.__annotations__.keys():
            value = getattr(self, attr)
            if isinstance(value, Pipeline):
                pipelines[attr] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    pipelines[f"{attr}.{k}"] = v

        return pipelines

    # noinspection PyMethodParameters
    @root_validator
    def ensure_at_least_one_pipeline(cls, values: dict):
        if not any(bool(v) for v in values.values()):
            raise ValueError("There must be at least one pipeline")

        return values


class PipelineSpec(BaseModel):
    image: ImageType = None
    definitions: Optional[Definitions] = Field(default_factory=Definitions.construct)
    clone_settings: Optional[CloneSettings] = Field(default_factory=CloneSettings.empty)
    pipelines: Pipelines

    @property
    def caches(self):
        return self.definitions.caches

    @property
    def services(self):
        return self.definitions.services

    def get_pipeline(self, name: str) -> Optional[Pipeline]:
        return self.pipelines.get_all().get(name)

    def get_available_pipelines(self) -> List[str]:
        return list(self.pipelines.get_all().keys())

    # noinspection PyMethodParameters
    @validator("image")
    def convert_str_image_to_object(cls, value):
        if isinstance(value, str):
            return Image(name=value)

        return value


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
    def __init__(self, exit_code: int, build_number: int, pipeline_uuid: UUID):
        self.exit_code = exit_code
        self.build_number = build_number
        self.pipeline_uuid = pipeline_uuid

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
