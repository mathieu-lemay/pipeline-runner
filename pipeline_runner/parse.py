import os.path

import yaml

from .config import config
from .models import Cache, Image, ParallelStep, Pipeline, Pipelines, Service, Step

try:
    from yaml import CLoader as YamlLoader
except ImportError:
    # noinspection PyUnresolvedReferences
    from yaml import YamlLoader


class ParseError(Exception):
    pass


class PipelinesFileParser:
    def __init__(self, file_path: str):
        self._file_path = file_path

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

        return Pipelines(image, pipelines, caches, services)

    def _parse_pipelines(self, data):
        if "pipelines" not in data:
            raise ParseError("Invalid pipelines file: Key not found: 'pipelines'")

        pipeline_groups = data["pipelines"]

        group_names = set(pipeline_groups.keys())

        if not group_names:
            raise ParseError("No pipeline groups")

        invalid_groups = group_names - {"branches", "custom", "pull-requests"}
        if invalid_groups:
            raise ParseError(f"Invalid groups: {invalid_groups}")

        pipelines = {}

        for g in group_names:
            for name, values in pipeline_groups[g].items():
                path = f"{g}.{name}"
                pipelines[path] = self._parse_pipeline(path, name, values)

        return pipelines

    def _parse_pipeline(self, path, name, elements):
        if not isinstance(elements, list):
            raise ValueError(f"Invalid elements for step: {name}")

        steps = []

        # TODO: Variables

        for element in elements:
            if "step" in element:
                steps.append(self._parse_step(element["step"]))
            elif "parallel" in element:
                steps.append(self._parse_parallel_step(element["parallel"]))
            elif "variables" in element:
                # variables.append(self._parse_variables(element['variables']))
                pass
            else:
                raise ValueError(f"Invalid element for pipeline: {element}")

        return Pipeline(path, name, steps)

    def _parse_step(self, values):
        image = values.get("image")
        if image:
            image = self._parse_image(image)

        services = values.get("services", [])
        if len(services) > 5:
            raise ValueError("Too many services. Enforcing a limit of 5 services per step.")

        size = self._parse_step_size(values.get("size"))

        step = Step(
            values["name"],
            values["script"],
            image,
            values.get("caches"),
            services,
            values.get("artifacts"),
            values.get("after-script"),
            size,
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
    def _parse_step_size(value):
        if not value:
            return 1
        elif value == "2x":
            return 2
        else:
            raise ValueError(f"Invalid size: {value}")

    def _parse_image(self, value):
        if isinstance(value, str):
            return Image(value)

        name = value["name"]
        username = expandvars(value.get("username"))
        password = expandvars(value.get("password"))
        email = expandvars(value.get("email"))
        user = expandvars(value.get("run-as-user"))
        aws = self._parse_aws_credentials(value)

        return Image(name, username, password, email, user, aws)

    @staticmethod
    def _parse_aws_credentials(value):
        if "aws" not in value:
            return None

        creds = value["aws"]

        access_key = expandvars(creds.get("access-key"))
        secret_key = expandvars(creds.get("secret-key"))

        return {
            "access-key": access_key,
            "secret-key": secret_key,
        }

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


def expandvars(value):
    if value is None:
        return None

    value = os.path.expandvars(value)

    if "$" in value:
        raise ValueError(f"Missing envvars: {value}")

    return value
