import os.path
from typing import Dict, Optional

import yaml

from .config import config
from .models import Cache, CloneSettings, Image, ParallelStep, Pipeline, Pipelines, Service, Step, Trigger

try:
    from yaml import CLoader as YamlLoader
except ImportError:
    # noinspection PyUnresolvedReferences
    from yaml import YamlLoader


class ParseError(Exception):
    pass


class PipelinesFileParser:
    def __init__(self, file_path: str, *_, expand_vars=True):
        self._file_path = file_path
        self._expand_vars = expand_vars

    def parse(self):
        if not os.path.isfile(self._file_path):
            raise ValueError(f"Pipelines file not found: {self._file_path}")

        with open(self._file_path) as f:
            pipelines_data = yaml.load(f, Loader=YamlLoader)

        caches, services = self._parse_definitions(pipelines_data)
        pipelines = self._parse_pipelines(pipelines_data)

        if "image" in pipelines_data:
            image = self._parse_image(pipelines_data["image"])
        else:
            image = None

        if "clone" in pipelines_data:
            clone_settings = self._parse_clone_settings(pipelines_data["clone"])
        else:
            clone_settings = None

        return Pipelines(image, pipelines, caches, services, clone_settings)

    def _parse_pipelines(self, data):
        if "pipelines" not in data:
            raise ParseError("Invalid pipelines file: Key not found: 'pipelines'")

        pipeline_groups = data["pipelines"]

        group_names = set(pipeline_groups.keys())

        if not group_names:
            raise ParseError("No pipeline groups")

        invalid_groups = group_names - {"default", "branches", "custom", "pull-requests"}
        if invalid_groups:
            raise ParseError(f"Invalid groups: {invalid_groups}")

        pipelines = {}

        for g in group_names:
            if g == "default":
                pipelines[g] = self._parse_pipeline(g, "default", pipeline_groups[g])
            else:
                for name, values in pipeline_groups[g].items():
                    path = f"{g}.{name}"
                    pipelines[path] = self._parse_pipeline(path, name, values)

        return pipelines

    def _parse_pipeline(self, path, name, elements):
        if not isinstance(elements, list):
            raise ValueError(f"Invalid elements for step: {name}")

        steps = []
        variables = {}

        for element in elements:
            if "step" in element:
                steps.append(self._parse_step(element["step"]))
            elif "parallel" in element:
                steps.append(self._parse_parallel_step(element["parallel"]))
            elif "variables" in element:
                variables.update(self._parse_variables(element["variables"]))
            else:
                raise ValueError(f"Invalid element for pipeline: {element}")

        return Pipeline(path, name, steps, variables)

    def _parse_step(self, values):
        image = self._parse_image(values.get("image"))

        services = values.get("services", [])
        if len(services) > 5:
            raise ValueError("Too many services. Enforcing a limit of 5 services per step.")

        clone = values.get("clone")
        if clone:
            clone_settings = self._parse_clone_settings(clone)
        else:
            clone_settings = None

        step = Step(
            values["name"],
            values["script"],
            image,
            values.get("caches"),
            services,
            values.get("artifacts"),
            values.get("after-script"),
            self._parse_step_size(values.get("size")),
            clone_settings,
            values.get("deployment"),
            self._parse_trigger(values.get("trigger")),
        )

        return step

    def _parse_parallel_step(self, items):
        if not isinstance(items, list):
            raise ValueError(f"Invalid elements for parallel step: {items}")

        steps = []

        for item in items:
            if "step" not in item:
                raise ValueError(f"Invalid element for parallel step: {item}")

            steps.append(self._parse_step(item["step"]))

        return ParallelStep(steps)

    @staticmethod
    def _parse_variables(items) -> Dict[str, str]:
        if not isinstance(items, list):
            raise ValueError(f"Invalid elements for variables: {items}")

        return {i["name"]: None for i in items}

    @staticmethod
    def _parse_step_size(value):
        if not value:
            return 1
        elif value == "2x":
            return 2
        else:
            raise ValueError(f"Invalid size: {value}")

    @staticmethod
    def _parse_trigger(value) -> Trigger:
        if value in (None, "automatic"):
            return Trigger.Automatic
        elif value == "manual":
            return Trigger.Manual
        else:
            raise ValueError(f"Invalid trigger: {value}")

    def _parse_image(self, value) -> Optional[Image]:
        if not value:
            return None

        if isinstance(value, str):
            return Image(value)

        name = value["name"]
        username = self._expandvars(value.get("username"))
        password = self._expandvars(value.get("password"))
        email = self._expandvars(value.get("email"))
        run_as_user = int(value["run-as-user"]) if "run-as-user" in value else None
        aws = self._parse_aws_credentials(value)

        return Image(name, username, password, email, run_as_user, aws)

    def _parse_aws_credentials(self, value):
        if "aws" not in value:
            return None

        creds = value["aws"]

        access_key = self._expandvars(creds.get("access-key"))
        secret_key = self._expandvars(creds.get("secret-key"))

        return {
            "access-key": access_key,
            "secret-key": secret_key,
        }

    def _parse_clone_settings(self, value):
        cs = CloneSettings.default()

        if "depth" in value:
            cs.depth = self._parse_clone_depth(value["depth"])

        if "lfs" in value:
            cs.lfs = self._parse_boolean(value["lfs"])

        if "enabled" in value:
            cs.enabled = self._parse_boolean(value["enabled"])

        return cs

    @staticmethod
    def _parse_clone_depth(value) -> Optional[int]:
        if value == "full":
            return 0
        elif isinstance(value, int) and value > 0:
            return value

        raise ValueError(f"Invalid value for 'depth': {value}")

    def _parse_definitions(self, data):
        caches = {}
        services = {}

        for name, path in config.default_caches.items():
            caches[name] = Cache(name, path)

        for name, values in config.default_services.items():
            services[name] = self._parse_service(name, values)

        if "definitions" not in data:
            return caches, services

        definitions = data["definitions"]

        for name, path in definitions.get("caches", {}).items():
            caches[name] = Cache(name, path)

        for name, values in definitions.get("services", {}).items():
            service = self._parse_service(name, values)

            if name in services:
                services[name].update(service)
            else:
                services[name] = service

        for s in services.values():
            if not s.image:
                raise ValueError(f"No image for service: {s.name}")

        return caches, services

    def _parse_service(self, name, values):
        if "image" in values:
            image = self._parse_image(values["image"])
        else:
            image = None

        environment = values.get("environment")
        memory = int(values.get("memory", config.service_container_default_memory_limit))
        command = values.get("command")

        return Service(name, image, environment, memory, command)

    @staticmethod
    def _parse_boolean(value) -> bool:
        if not isinstance(value, bool):
            raise TypeError(f"Not a valid boolean: {value}")

        return value

    def _expandvars(self, value):
        if not self._expand_vars:
            return value

        if value is None:
            return None

        value = os.path.expandvars(value)

        if "$" in value:
            raise ValueError(f"Missing envvars: {value}")

        return value
