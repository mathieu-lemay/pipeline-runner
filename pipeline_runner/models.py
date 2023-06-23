import os.path
from enum import Enum
from string import Template
from typing import Any, Generic, Iterator, Optional, Sequence, SupportsIndex, TypeVar, Union
from uuid import UUID, uuid4

from git.repo import Repo
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Extra, Field, ValidationError, root_validator, validator
from pydantic.error_wrappers import ErrorWrapper
from slugify import slugify

from . import utils
from .config import config
from .utils import generate_ssh_rsa_key


class BaseModel(PydanticBaseModel):
    __env_var_expand_fields__: Sequence[str]

    class Config:
        extra = Extra.forbid
        allow_population_by_field_name = True

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for attr in self.__env_var_expand_fields__:
            value = getattr(self, attr)
            if value is None:
                continue

            if isinstance(value, str):
                value = Template(value).substitute(variables)
                setattr(self, attr, value)
            elif isinstance(value, BaseModel):
                value.expand_env_vars(variables)


class AwsCredentials(BaseModel):
    access_key_id: str = Field(alias="access-key")
    secret_access_key: str = Field(alias="secret-key")
    oidc_role: Optional[str] = Field(alias="oidc-role")

    __env_var_expand_fields__: Sequence[str] = ["access_key_id", "secret_access_key", "oidc_role"]

    @validator("oidc_role")
    def oidc_role_not_supported(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            raise ValueError("aws oidc-role not supported")

        return v


class Image(BaseModel):
    name: str
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    run_as_user: Optional[str] = Field(None, alias="run-as-user")
    aws: Optional[AwsCredentials] = None

    __env_var_expand_fields__: Sequence[str] = ["username", "password", "email", "aws"]


ImageType = Optional[Union[str, Image]]


class Service(BaseModel):
    image: Optional[Image] = None
    variables: dict[str, str] = Field(default_factory=dict, alias="environment")
    memory: int = 1024

    @validator("image", pre=True)
    def convert_str_image_to_object(cls, value: Union[Image, str]) -> Image:
        if isinstance(value, str):
            return Image(name=value)

        return value

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        if self.image:
            self.image.expand_env_vars(variables)

        for k, v in self.variables.items():
            self.variables[k] = Template(v).substitute(variables)


class Definitions(BaseModel):
    caches: dict[str, str] = Field(default_factory=dict)
    services: dict[str, Service] = Field(default_factory=dict)

    @validator("services")
    def ensure_default_services_have_no_image_and_non_default_services_have_an_image(
        cls, value: dict[str, Service]
    ) -> dict[str, Service]:
        errors = []

        for service_name, service in value.items():
            if service_name in config.default_services and service.image is not None:
                errors.append(
                    ErrorWrapper(ValueError(f"Default service {service_name} can't have an image"), loc=service_name)
                )
            elif service_name not in config.default_services and service.image is None:
                errors.append(ErrorWrapper(ValueError(f"Service {service_name} must have an image"), loc=service_name))

        if errors:
            raise ValidationError(errors, cls)

        return value

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.services.values():
            s.expand_env_vars(variables)


class CloneSettings(BaseModel):
    depth: Optional[Union[str, int]] = 50
    lfs: Optional[bool] = False
    enabled: Optional[bool] = True

    @classmethod
    def empty(cls) -> "CloneSettings":
        return CloneSettings(depth=None, lfs=None, enabled=None)

    @validator("depth")
    def validate_depth(cls, value: Optional[Union[str, int]]) -> Optional[Union[str, int]]:
        if value is None:
            return None

        if isinstance(value, str):
            if value == "full":
                return 0

            raise ValueError(f"Not a valid value: {value}. Valid values are: ['full']")
        elif isinstance(value, int):
            if value <= 0:
                raise ValueError(f"depth {value} is not a positive integer")

            return value

        raise TypeError(f"Invalid type for 'depth': {type(value)}")


class Trigger(str, Enum):
    Automatic = "automatic"
    Manual = "manual"


class StepSize(str, Enum):
    Simple = "1x"
    Double = "2x"

    def as_int(self) -> int:
        return {self.Simple: 1, self.Double: 2}[self]


class Pipe(BaseModel):
    pipe: str
    variables: dict[str, str] = Field(default_factory=dict)

    def as_cmd(self) -> str:
        cmd = "docker run --rm"
        if self.variables:
            cmd += " " + " ".join(f'-e {k}="{self._escape_value(v)}"' for k, v in self.variables.items())

        return f"{cmd} {self.get_image()}"

    @staticmethod
    def _escape_value(v: str) -> str:
        return v.replace('"', '\\"')

    def get_image(self) -> str:
        if self.pipe.startswith("atlassian/"):
            return self.pipe.replace("atlassian/", "bitbucketpipelines/", 1)

        return self.pipe


class Step(BaseModel):
    name: str = "<unnamed>"
    script: list[Union[str, Pipe]]
    image: Optional[Image] = None
    caches: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    after_script: list[Union[str, Pipe]] = Field(default_factory=list, alias="after-script")
    size: StepSize = StepSize.Simple
    clone_settings: CloneSettings = Field(default_factory=CloneSettings.empty, alias="clone")
    deployment: Optional[str] = None
    trigger: Trigger = Trigger.Automatic
    max_time: Optional[int] = Field(None, alias="max-time")

    __env_var_expand_fields__: Sequence[str] = ["image"]

    @validator("image", pre=True)
    def convert_str_image_to_object(cls, value: Union[Image, str]) -> Image:
        if isinstance(value, str):
            return Image(name=value)

        return value


T = TypeVar("T")


class ListWrapper(BaseModel, Generic[T]):
    wrapped: list[T]

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        return iter(self.wrapped)

    def __getitem__(self, item: SupportsIndex) -> T:
        return self.wrapped[item]

    def __len__(self) -> int:
        return len(self.wrapped)


class StepWrapper(BaseModel):
    step: Step = Field(alias="step")

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        self.step.expand_env_vars(variables)

    def __getattr__(self, item: str) -> Any:
        if item in self.__dict__:
            return self.__dict__[item]
        else:
            return getattr(self.step, item)


class ParallelStep(ListWrapper[StepWrapper]):
    wrapped: list[StepWrapper] = Field(alias="parallel", min_items=2)

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.wrapped:
            s.expand_env_vars(variables)


class Variable(BaseModel):
    name: str
    default: Optional[str]
    allowed_values: Optional[list[str]] = Field(alias="allowed-values")

    @root_validator
    def validate_var_with_allowed_values_must_have_a_default_value(cls, values: dict[str, Any]) -> dict[str, Any]:
        allowed_values = values["allowed_values"]
        default = values["default"]

        if allowed_values:
            if not default:
                raise ValueError(
                    "The variable default value is not provided. "
                    "A default value is required if allowed values list is specified."
                )

            if default not in allowed_values:
                raise ValueError(f'The variable allowed values list doesn\'t contain a default value "{default}".')

        return values


class Variables(ListWrapper[Variable]):
    wrapped: list[Variable] = Field(alias="variables")


PipelineElement = Union[StepWrapper, ParallelStep, Variables]


class Pipeline(BaseModel):
    __root__: list[PipelineElement] = Field(min_items=1)

    @validator("__root__")
    def validate_variables_must_be_first_element_of_list_if_present(
        cls, pipeline_items: list[PipelineElement]
    ) -> list[PipelineElement]:
        if any(i for i in pipeline_items[1:] if isinstance(i, Variables)):
            raise ValueError("'variables' can only be the first element of the list.")

        return pipeline_items

    def get_variables(self) -> Variables:
        if isinstance(self.__root__[0], Variables):
            return self.__root__[0]

        return Variables(wrapped=[])

    def get_steps(self) -> list[Union[StepWrapper, ParallelStep]]:
        return [i for i in self.__root__ if not isinstance(i, Variables)]

    def __iter__(self) -> Iterator[PipelineElement]:  # type: ignore[override]
        return iter(self.__root__)

    def __getitem__(self, item: SupportsIndex) -> PipelineElement:
        return self.__root__[item]

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.get_steps():
            s.expand_env_vars(variables)


class Pipelines(BaseModel):
    default: Optional[Pipeline] = None
    branches: dict[str, Pipeline] = Field(default_factory=dict)
    pull_requests: dict[str, Pipeline] = Field(default_factory=dict, alias="pull-requests")
    custom: dict[str, Pipeline] = Field(default_factory=dict)

    def get_all(self) -> dict[str, Pipeline]:
        pipelines = {}
        for attr in self.__annotations__.keys():
            value = getattr(self, attr)
            if isinstance(value, Pipeline):
                pipelines[attr] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    pipelines[f"{attr}.{k}"] = v

        return pipelines

    @root_validator
    def ensure_at_least_one_pipeline(cls, values: dict[str, Any]) -> dict[str, Any]:
        if not any(bool(v) for v in values.values()):
            raise ValueError("There must be at least one pipeline")

        return values

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for p in self.get_all().values():
            p.expand_env_vars(variables)


class PipelineSpec(BaseModel):
    image: Optional[Image] = None
    definitions: Definitions = Field(default_factory=Definitions.construct)
    clone_settings: CloneSettings = Field(default_factory=CloneSettings.empty, alias="clone")
    pipelines: Pipelines

    __env_var_expand_fields__: Sequence[str] = ["image", "definitions", "pipelines"]

    class Config:
        extra = Extra.ignore

    @property
    def caches(self) -> dict[str, str]:
        return self.definitions.caches

    @property
    def services(self) -> dict[str, Service]:
        return self.definitions.services

    def get_pipeline(self, name: str) -> Optional[Pipeline]:
        return self.pipelines.get_all().get(name)

    def get_available_pipelines(self) -> list[str]:
        return list(self.pipelines.get_all().keys())

    @validator("image", pre=True)
    def convert_str_image_to_object(cls, value: Union[Image, str]) -> Image:
        if isinstance(value, str):
            return Image(name=value)

        return value


class ProjectMetadata(BaseModel):
    name: str
    path_slug: str
    slug: str
    key: str
    project_uuid: UUID = Field(default_factory=uuid4)
    repo_uuid: UUID = Field(default_factory=uuid4)
    build_number: int = 0
    ssh_key: str = Field(default_factory=generate_ssh_rsa_key)

    @classmethod
    def load_from_file(cls, project_directory: str) -> "ProjectMetadata":
        path_slug = utils.hashify_path(project_directory)

        project_data_dir = utils.get_project_data_directory(path_slug)
        fp = os.path.join(project_data_dir, "meta.json")

        if os.path.exists(fp):
            meta = cls.parse_file(fp)
        else:
            name = os.path.basename(project_directory)
            slug = slugify(name)
            key = "".join(s[0].upper() for s in slug.split("-"))
            meta = cls(name=name, path_slug=path_slug, slug=slug, key=key)

        meta.build_number += 1

        with open(fp, "w") as f:
            f.write(meta.json())

        return meta


class Repository:
    def __init__(self, path: str):
        self.path = path
        self._git_repo = Repo(path)

    def get_current_branch(self) -> str:
        return self._git_repo.active_branch.name

    def get_current_commit(self) -> str:
        return self._git_repo.head.commit.hexsha


class PipelineResult:
    def __init__(self, exit_code: int, build_number: int, pipeline_uuid: UUID):
        self.exit_code = exit_code
        self.build_number = build_number
        self.pipeline_uuid = pipeline_uuid

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
