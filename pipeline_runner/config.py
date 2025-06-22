import getpass
import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, cast

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __name__ as __project_name__

if TYPE_CHECKING:
    from .models import CacheType

DEFAULT_IMAGE: Final[str] = "atlassian/default-image:latest"

DEFAULT_CACHES: Final[dict[str, "CacheType"]] = {
    "composer": "~/.composer/cache",
    "dotnetcore": "~/.nuget/packages",
    "gradle": "~/.gradle/caches ",
    "ivy2": "~/.ivy2/cache",
    "maven": "~/.m2/repository",
    "node": "node_modules",
    "pip": "~/.cache/pip",
    "sbt": "~/.sbt",
}

DEFAULT_SERVICES: Mapping[str, Any] = MappingProxyType(
    {
        "docker": {
            "image": (
                "docker-public.packages.atlassian.com/sox/atlassian"
                "/bitbucket-pipelines-docker-daemon:v25.0.3-prod-stable"
            ),
            "memory": 1024,
        }
    }
)

# This is the version of the client that is injected in the container if docker is not already present.
# It is _not_ the same as the docker daemon version.
ATLASSIAN_DOCKER_CLI_VERSION = "20.10.24"


class OIDCSettings(BaseSettings):
    enabled: bool = False
    issuer: str = "https://example.org"
    audience: str = "rogueconsulting::pipeline-runner"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="pipeline_runner_", env_nested_delimiter="_")

    color: bool = True
    cpu_limits: bool = False
    expose_ssh_agent: bool = False
    volumes: list[str] = Field(default_factory=list)

    username: str = Field(default_factory=getpass.getuser)

    oidc: OIDCSettings = Field(default_factory=OIDCSettings)

    total_memory_limit: int = 4096
    build_container_minimum_memory: int = 1024
    service_container_default_memory_limit: int = 1024

    log_level: str = Field(alias="PIPELINE_LOG_LEVEL", default="DEBUG")

    remote_base_dir: str = "/opt/atlassian"
    remote_workspace_dir: str = "/opt/atlassian/workspace"
    remote_pipeline_dir: str = "/opt/atlassian/pipelines/agent"
    build_dir: str = "/opt/atlassian/pipelines/agent/build"
    scripts_dir: str = "/opt/atlassian/pipelines/agent/scripts"
    temp_dir: str = "/opt/atlassian/pipelines/agent/temp"
    caches_dir: str = "/opt/atlassian/pipelines/agent/caches"
    ssh_key_dir: str = "/opt/atlassian/pipelines/agent/ssh"

    @field_validator("log_level")
    def validate_log_level(cls, value: str) -> str:
        return cast("str", logging.getLevelName(value.upper()))

    @field_validator("volumes")
    def validate_volumes(cls, volumes: list[str]) -> list[str]:
        errors = []

        for idx, vol in enumerate(volumes):
            if not vol:
                errors.append(f"Invalid volume: {vol}: empty volume spec")

            parts = vol.split(":")
            if len(parts) > 3:  # noqa: PLR2004  # Magic value used in comparison
                errors.append(f"Invalid volume: {vol}: invalid volume spec")

            parts[0] = os.path.expandvars(os.path.expanduser(parts[0]))

            volumes[idx] = ":".join(parts)

        if errors:
            raise ValueError(", ".join(errors))

        return volumes

    @property
    def log_config(self) -> dict[str, Any]:
        log_handler_name = "colored" if self.color and "NO_COLOR" not in os.environ else "default"
        return {
            "version": 1,
            "loggers": {
                __project_name__: {"handlers": [log_handler_name], "level": self.log_level},
                "docker": {"handlers": ["default"], "level": "INFO"},
            },
            "handlers": {
                "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
                "colored": {"formatter": "colored", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
            },
            "formatters": {
                "default": {
                    "format": "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "colored": {
                    "()": "coloredlogs.ColoredFormatter",
                    "format": "%(asctime)s.%(msecs)03d %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
        }


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()


class ConfigProxy:
    def __getattr__(self, name: str) -> Any:  # noqa: ANN401  # Proxy to `Config`
        return getattr(get_config(), name)


config = cast("Config", ConfigProxy())
