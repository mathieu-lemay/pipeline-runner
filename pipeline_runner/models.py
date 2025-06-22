import logging
import os.path
from collections.abc import Iterator, Sequence
from enum import Enum
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any, Generic, SupportsIndex, TypeVar
from uuid import UUID, uuid4

from git.repo import Repo
from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.root_model import RootModel
from pydantic_core import InitErrorDetails, PydanticCustomError
from slugify import slugify

from . import utils
from .config import DEFAULT_SERVICES, config
from .utils import generate_rsa_key

if TYPE_CHECKING:
    import sys

    if sys.version_info < (3, 11):
        from typing_extensions import Self
    else:
        from typing import Self

logger = logging.getLogger(__name__)


class BaseModel(PydanticBaseModel):
    __env_var_expand_fields__: Sequence[str]

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

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
    access_key_id: str | None = Field(alias="access-key", default=None)
    secret_access_key: str | None = Field(alias="secret-key", default=None)
    oidc_role: str | None = Field(alias="oidc-role", default=None)

    __env_var_expand_fields__: Sequence[str] = ["access_key_id", "secret_access_key", "oidc_role"]

    @model_validator(mode="after")
    def validate_aws_auth(self) -> "Self":
        if config.oidc.enabled and self.oidc_role is not None:
            return self

        if self.access_key_id is None or self.secret_access_key is None:
            raise ValueError("aws image authentication requires 'access_key_id' and 'secret_access_key'")

        return self

    @field_validator("oidc_role")
    def oidc_role_not_supported(cls, v: str | None) -> str | None:
        if config.oidc.enabled is False and v is not None:
            raise ValueError("aws oidc-role not supported")

        return v


class Image(BaseModel):
    name: str
    username: str | None = None
    password: str | None = None
    email: str | None = None
    run_as_user: str | None = Field(None, alias="run-as-user")
    aws: AwsCredentials | None = None

    __env_var_expand_fields__: Sequence[str] = ["username", "password", "email", "aws"]

    @field_validator("run_as_user", mode="before")
    def parse_run_as_user(cls, value: str | int) -> str:
        if isinstance(value, int):
            return str(value)

        return value


ImageType = Image | str | None


class Service(BaseModel):
    image: Image | None = None
    variables: dict[str, str] = Field(default_factory=dict, alias="environment")
    memory: int = 1024

    @field_validator("image", mode="before")
    def convert_str_image_to_object(cls, value: Image | str) -> Image:
        if isinstance(value, str):
            return Image(name=value)

        return value

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        if self.image:
            self.image.expand_env_vars(variables)

        for k, v in self.variables.items():
            self.variables[k] = Template(v).substitute(variables)


class CacheKey(BaseModel):
    files: list[str]


class Cache(BaseModel):
    key: CacheKey
    path: str

    def __hash__(self) -> int:
        return (*self.key.files, self.path).__hash__()


CacheType = Cache | str


class Definitions(BaseModel):
    caches: dict[str, CacheType] = Field(default_factory=dict)
    services: dict[str, Service] = Field(default_factory=dict)

    @field_validator("services")
    def ensure_default_services_have_no_image_and_non_default_services_have_an_image(
        cls, value: dict[str, Service]
    ) -> dict[str, Service]:
        errors = []

        for service_name, service in value.items():
            if service_name in DEFAULT_SERVICES and service.image is not None:
                logger.warning(
                    "Using custom image for service '%s'. This is not officially supported and can lead to issues",
                    service_name,
                )
            elif service_name not in DEFAULT_SERVICES and service.image is None:
                err = PydanticCustomError(
                    "missing",
                    "Service '{service_name}' must have an image",
                    {"service_name": service_name},
                )
                errors.append(
                    InitErrorDetails(
                        type=err,
                        loc=(service_name, "image"),
                        input=service,
                    )
                )

        if errors:
            raise ValidationError.from_exception_data(title=cls.__class__.__name__, line_errors=errors)

        return value

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.services.values():
            s.expand_env_vars(variables)


class CloneSettings(BaseModel):
    depth: str | int | None = 50
    lfs: bool | None = False
    enabled: bool | None = True

    @classmethod
    def empty(cls) -> "CloneSettings":
        return CloneSettings(depth=None, lfs=None, enabled=None)

    @field_validator("depth")
    def validate_depth(cls, value: str | int | None) -> str | int | None:
        if value is None:
            return None

        if isinstance(value, str):
            if value == "full":
                return 0

            raise ValueError(f"Not a valid value: {value}. Valid values are: ['full']")

        if isinstance(value, int):
            if value <= 0:
                raise ValueError(f"depth {value} is not a positive integer")

            return value

        raise TypeError(f"Invalid type for 'depth': {type(value)}")


class Trigger(str, Enum):
    __slots__ = ()

    Automatic = "automatic"
    Manual = "manual"


class StepSize(str, Enum):
    __slots__ = ()

    Size1 = "1x"
    Size2 = "2x"
    Size4 = "4x"
    Size8 = "8x"

    def as_int(self) -> int:
        return {self.Size1: 1, self.Size2: 2, self.Size4: 4, self.Size8: 8}[self]


class Changesets(BaseModel):
    include_paths: list[str] = Field(alias="includePaths", min_length=1)


class Condition(BaseModel):
    changesets: Changesets


class Pipe(BaseModel):
    pipe: str
    variables: dict[str, str | list[str]] = Field(default_factory=dict)

    def as_cmd(self) -> str:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--volume=/opt/atlassian/pipelines/agent/build:/opt/atlassian/pipelines/agent/build",
            "--volume=/opt/atlassian/pipelines/agent/ssh:/opt/atlassian/pipelines/agent/ssh:ro",
            "--volume=/opt/atlassian/pipelines/bin/docker:/usr/local/bin/docker:ro",
            "--workdir=$(pwd)",
            "--label=org.bitbucket.pipelines.system=true",
            '--env=BITBUCKET_STEP_TRIGGERER_UUID="$BITBUCKET_STEP_TRIGGERER_UUID"',
            '--env=BITBUCKET_REPO_FULL_NAME="$BITBUCKET_REPO_FULL_NAME"',
            '--env=BITBUCKET_GIT_HTTP_ORIGIN="$BITBUCKET_GIT_HTTP_ORIGIN"',
            '--env=BITBUCKET_REPO_SLUG="$BITBUCKET_REPO_SLUG"',
            '--env=BITBUCKET_PROJECT_UUID="$BITBUCKET_PROJECT_UUID"',
            '--env=CI="$CI"',
            '--env=BITBUCKET_REPO_OWNER="$BITBUCKET_REPO_OWNER"',
            '--env=BITBUCKET_REPO_IS_PRIVATE="$BITBUCKET_REPO_IS_PRIVATE"',
            '--env=BITBUCKET_WORKSPACE="$BITBUCKET_WORKSPACE"',
            '--env=BITBUCKET_SSH_KEY_FILE="$BITBUCKET_SSH_KEY_FILE"',
            '--env=BITBUCKET_REPO_OWNER_UUID="$BITBUCKET_REPO_OWNER_UUID"',
            '--env=BITBUCKET_STEP_RUN_NUMBER="$BITBUCKET_STEP_RUN_NUMBER"',
            '--env=BITBUCKET_BUILD_NUMBER="$BITBUCKET_BUILD_NUMBER"',
            '--env=BITBUCKET_BRANCH="$BITBUCKET_BRANCH"',
            '--env=BITBUCKET_GIT_SSH_ORIGIN="$BITBUCKET_GIT_SSH_ORIGIN"',
            '--env=BITBUCKET_PIPELINE_UUID="$BITBUCKET_PIPELINE_UUID"',
            '--env=BITBUCKET_PIPELINES_VARIABLES_PATH="$BITBUCKET_PIPELINES_VARIABLES_PATH"',
            '--env=BITBUCKET_COMMIT="$BITBUCKET_COMMIT"',
            '--env=BITBUCKET_REPO_UUID="$BITBUCKET_REPO_UUID"',
            '--env=BITBUCKET_CLONE_DIR="$BITBUCKET_CLONE_DIR"',
            '--env=BITBUCKET_PROJECT_KEY="$BITBUCKET_PROJECT_KEY"',
            '--env=PIPELINES_JWT_TOKEN="$PIPELINES_JWT_TOKEN"',
            '--env=BITBUCKET_STEP_UUID="$BITBUCKET_STEP_UUID"',
            '--env=BITBUCKET_DOCKER_HOST_INTERNAL="$BITBUCKET_DOCKER_HOST_INTERNAL"',
            '--env=DOCKER_HOST="tcp://host.docker.internal:2375"',
        ]

        variables = self.expand_variables()
        if variables:
            cmd += [f'-e {k}="{self._escape_value(v)}"' for k, v in variables.items()]

        cmd.append(self.get_image())

        return " ".join(cmd)

    def expand_variables(self) -> dict[str, str]:
        expanded_variables = {}

        for key, value in self.variables.items():
            if isinstance(value, list):
                for i, v in enumerate(value):
                    expanded_variables[f"{key}_{i}"] = v

                expanded_variables[f"{key}_COUNT"] = str(len(value))
            else:
                expanded_variables[key] = value

        return expanded_variables

    @staticmethod
    def _escape_value(v: str) -> str:
        return v.replace('"', '\\"')

    def get_image(self) -> str:
        if self.pipe.startswith("atlassian/"):
            return self.pipe.replace("atlassian/", "bitbucketpipelines/", 1)

        return self.pipe


class Artifacts(BaseModel):
    download: bool = True
    paths: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def list_to_object(cls, data: Any) -> Any:  # noqa: ANN401  # pydantic expects `Any`
        if isinstance(data, list):
            return {"paths": data}

        return data


class Step(BaseModel):
    name: str = "<unnamed>"
    script: list[str | Pipe]
    image: Image | None = None
    caches: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    artifacts: Artifacts = Field(default_factory=Artifacts)
    after_script: list[str | Pipe] = Field(default_factory=list, alias="after-script")
    size: StepSize = StepSize.Size1
    clone_settings: CloneSettings = Field(default_factory=CloneSettings.empty, alias="clone")
    deployment: str | None = None
    trigger: Trigger = Trigger.Automatic
    max_time: int | None = Field(None, alias="max-time")
    condition: Condition | None = None
    oidc: bool = False

    __env_var_expand_fields__: Sequence[str] = ["image"]

    @field_validator("image", mode="before")
    def convert_str_image_to_object(cls, value: Image | str) -> Image:
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

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401  # Dynamically typed expressions (typing.Any) are disallowed
        if item in self.__dict__:
            return self.__dict__[item]

        return getattr(self.step, item)


class ParallelSteps(ListWrapper[StepWrapper]):
    wrapped: list[StepWrapper] = Field(alias="steps", min_length=1)


class ParallelStep(BaseModel):
    parallel: list[StepWrapper] | ParallelSteps = Field(min_length=1)

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.parallel:
            s.expand_env_vars(variables)

    def __iter__(self) -> Iterator[StepWrapper]:  # type: ignore[override]
        return iter(self.parallel)

    def __getitem__(self, item: SupportsIndex) -> StepWrapper:
        return self.parallel[item]

    def __len__(self) -> int:
        return len(self.parallel)


class Variable(BaseModel):
    name: str
    default: str | None = None
    allowed_values: list[str] | None = Field(alias="allowed-values", default=None)

    @model_validator(mode="after")  # type: ignore[arg-type]
    @classmethod
    def validate_var_with_allowed_values_must_have_a_default_value(cls, model: "Variable") -> "Variable":
        allowed_values = model.allowed_values
        default = model.default

        if allowed_values:
            if not default:
                raise ValueError(
                    "The variable default value is not provided. "
                    "A default value is required if allowed values list is specified."
                )

            if default not in allowed_values:
                raise ValueError(f'The variable allowed values list doesn\'t contain a default value "{default}".')

        return model


class Variables(ListWrapper[Variable]):
    wrapped: list[Variable] = Field(alias="variables")


PipelineElement = StepWrapper | ParallelStep | Variables


class Pipeline(RootModel[list[PipelineElement]]):
    root: list[PipelineElement] = Field(min_length=1)

    @field_validator("root")
    def validate_variables_must_be_first_element_of_list_if_present(
        cls, pipeline_items: list[PipelineElement]
    ) -> list[PipelineElement]:
        if any(i for i in pipeline_items[1:] if isinstance(i, Variables)):
            raise ValueError("'variables' can only be the first element of the list.")

        return pipeline_items

    def get_variables(self) -> Variables:
        if isinstance(self.root[0], Variables):
            return self.root[0]

        return Variables(wrapped=[])

    def get_steps(self) -> list[StepWrapper | ParallelStep]:
        return [i for i in self.root if not isinstance(i, Variables)]

    def __iter__(self) -> Iterator[PipelineElement]:  # type: ignore[override]
        return iter(self.root)

    def __getitem__(self, item: SupportsIndex) -> PipelineElement:
        return self.root[item]

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for s in self.get_steps():
            s.expand_env_vars(variables)


class Pipelines(BaseModel):
    default: Pipeline | None = None
    branches: dict[str, Pipeline] = Field(default_factory=dict)
    pull_requests: dict[str, Pipeline] = Field(default_factory=dict, alias="pull-requests")
    custom: dict[str, Pipeline] = Field(default_factory=dict)
    tags: dict[str, Pipeline] = Field(default_factory=dict)
    bookmarks: dict[str, Pipeline] = Field(default_factory=dict)

    def get_all(self) -> dict[str, Pipeline]:
        pipelines = {}
        for attr in self.__annotations__:
            value = getattr(self, attr)
            if isinstance(value, Pipeline):
                pipelines[attr] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    pipelines[f"{attr}.{k}"] = v

        return pipelines

    @model_validator(mode="before")
    @classmethod
    def ensure_at_least_one_pipeline(cls, values: dict[str, Any]) -> dict[str, Any]:
        if not any(bool(v) for v in values.values()):
            raise ValueError("There must be at least one pipeline")

        return values

    def expand_env_vars(self, variables: dict[str, str]) -> None:
        for p in self.get_all().values():
            p.expand_env_vars(variables)


class PipelineSpec(BaseModel):
    image: Image | None = None
    definitions: Definitions = Field(default_factory=Definitions)
    clone_settings: CloneSettings = Field(default_factory=CloneSettings.empty, alias="clone")
    pipelines: Pipelines

    __env_var_expand_fields__: Sequence[str] = ["image", "definitions", "pipelines"]

    @property
    def caches(self) -> dict[str, CacheType]:
        return self.definitions.caches

    @property
    def services(self) -> dict[str, Service]:
        return self.definitions.services

    def get_pipeline(self, name: str) -> Pipeline | None:
        return self.pipelines.get_all().get(name)

    def get_available_pipelines(self) -> list[str]:
        return list(self.pipelines.get_all().keys())

    @field_validator("image", mode="before")
    def convert_str_image_to_object(cls, value: Image | str) -> Image:
        if isinstance(value, str):
            return Image(name=value)

        return value


class WorkspaceMetadata(BaseModel):
    workspace_uuid: UUID = Field(default_factory=uuid4)
    owner_uuid: UUID = Field(default_factory=uuid4)
    oidc_private_key: str = Field(default_factory=generate_rsa_key)

    @classmethod
    def load_from_file(cls, project_directory: str | None = None) -> "WorkspaceMetadata":
        meta_file = Path(utils.get_data_directory()) / "workspace.json"

        if meta_file.exists():
            meta = cls.model_validate_json(meta_file.read_text())
        elif project_directory:
            # TODO: Remove temporary load from project for migration purposes
            project_meta = ProjectMetadata.load_from_file(project_directory, increase_build=False)
            meta = cls(oidc_private_key=project_meta.oidc_private_key)
        else:
            meta = cls()

        meta_file.write_text(meta.model_dump_json())

        return meta


class ProjectMetadata(BaseModel):
    name: str
    path_slug: str
    slug: str
    key: str
    project_uuid: UUID = Field(default_factory=uuid4)
    repo_uuid: UUID = Field(default_factory=uuid4)
    build_number: int = 0
    ssh_key: str = Field(default_factory=generate_rsa_key)
    oidc_private_key: str = Field(default_factory=generate_rsa_key)

    @classmethod
    def load_from_file(cls, project_directory: str, *, increase_build: bool = True) -> "ProjectMetadata":
        # FIXME: increase_build is a ugly hack for something that should never have been done
        #        as part of this function in the first place.
        path_slug = utils.hashify_path(project_directory)

        project_data_dir = utils.get_project_data_directory(path_slug)
        meta_file = Path(project_data_dir) / "meta.json"

        if meta_file.exists():
            meta = cls.model_validate_json(meta_file.read_text())
        else:
            name = os.path.basename(project_directory)
            slug = slugify(name)
            key = "".join(s[0].upper() for s in slug.split("-"))
            meta = cls(name=name, path_slug=path_slug, slug=slug, key=key)

        if increase_build:
            meta.build_number += 1

        meta_file.write_text(meta.model_dump_json())

        return meta


class Repository:
    def __init__(self, path: str) -> None:
        self.path = path
        self._git_repo = Repo(path)

    def get_current_branch(self) -> str:
        return self._git_repo.active_branch.name

    def get_current_commit(self) -> str:
        return self._git_repo.head.commit.hexsha


class PipelineResult:
    def __init__(self, exit_code: int, build_number: int, pipeline_uuid: UUID) -> None:
        self.exit_code = exit_code
        self.build_number = build_number
        self.pipeline_uuid = pipeline_uuid

    @property
    def ok(self) -> bool:
        return self.exit_code == 0
