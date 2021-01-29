import os.path

import yaml

from .models import Cache, Image, ParallelStep, Pipeline, Pipelines, Service, Step

try:
    from yaml import CLoader as YamlLoader
except ImportError:
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
            yaml_data = os.path.expandvars(f.read())
            pipelines_data = yaml.load(yaml_data, Loader=YamlLoader)

        pipelines = self._parse_pipelines(pipelines_data)
        caches, services = self._parse_definitions(pipelines_data)

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

        invalid_groups = group_names - {"branches", "custom"}
        if invalid_groups:
            raise ParseError(f"Invalid groups: {invalid_groups}")

        pipelines = {}

        for g in group_names:
            for name, steps in pipeline_groups[g].items():
                path = f"{g}.{name}"
                pipelines[path] = Pipeline(path, name, self._parse_steps(steps))

        return pipelines

    def _parse_steps(self, step_list):
        steps = []
        for value in step_list:
            if "parallel" in value:
                value = value["parallel"]
                pstep = ParallelStep(self._parse_steps(value))
                steps.append(pstep)
                continue

            if "step" not in value:
                raise ValueError("Invalid step")

            value = value["step"]

            image = value.get("image")
            if image:
                image = self._parse_image(image)

            steps.append(
                Step(
                    value["name"],
                    value["script"],
                    image,
                    value.get("caches"),
                    value.get("services"),
                    value.get("artifacts"),
                    value.get("after-script"),
                )
            )

        return steps

    def _parse_image(self, value):
        if isinstance(value, str):
            return Image(value)

        name = value["name"]
        username = value.get("username")
        password = value.get("password")
        email = value.get("email")
        user = value.get("run-as-user")
        aws = value.get("aws")

        return Image(name, username, password, email, user, aws)

    def _parse_definitions(self, data):
        if "definitions" not in data:
            return None, None

        definitions = data["definitions"]
        caches = []
        services = []

        for name, path in definitions.get("caches", {}).items():
            caches.append(Cache(name, path))

        for name, value in definitions.get("services", {}).items():
            image = value.get("image")
            environment = value.get("environment")
            memory = int(value["memory"]) if "memory" in value else None

            services.append(Service(name, image, environment, memory))

        return caches, services
