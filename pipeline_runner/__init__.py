import json
import os.path
from typing import List, Optional, Union

import click
import yaml

try:
    from yaml import CLoader as YamlLoader
except ImportError:
    from yaml import YamlLoader


def dumps(*args, **kwargs):
    def _handler(obj):
        if isinstance(obj, DebugMixin):
            return obj.json()
        else:
            return None

    return json.dumps(*args, default=_handler, **kwargs)


class DebugMixin:
    def __repr__(self):
        values = [f"{k}: {repr(v)}" for k, v in self.__dict__.items() if k[0] != "_"]
        return f"{type(self).__name__} {{ {', '.join(values)} }}"

    def json(self):
        return {type(self).__name__: {k: v for k, v in self.__dict__.items() if k[0] != "_"}}


class PipelineService(DebugMixin):
    def __init__(self, name: str, image: str = None, environment: {str: str} = None, memory: int = None):
        self.name = name
        self.image = image
        self.environment = environment
        self.memory = memory


class PipelineCache(DebugMixin):
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path


class PipelineImage(DebugMixin):
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


class PipelineStep(DebugMixin):
    def __init__(self, name: str, script: [str], image: Optional[PipelineImage] = None):
        self.name = name
        self.script = script
        self.image = image


class PipelineParallelStep(DebugMixin):
    def __init__(self, *_, **_kw):
        pass


class Pipeline(DebugMixin):
    def __init__(self, path: str, name: str, steps: [Union[PipelineStep, PipelineParallelStep]], *_, **_kwargs):
        self.path = path
        self.name = name
        self.steps = steps


class Pipelines(DebugMixin):
    def __init__(
        self,
        image=None,
        pipelines: [Pipeline] = None,
        caches: [PipelineCache] = None,
        services: [PipelineService] = None,
    ):
        self.image = image
        self.pipelines = pipelines
        self.caches = caches
        self.services = services

    def get_pipeline(self, path) -> Optional[Pipeline]:
        return self.pipelines.get(path)


class PipelineParseError(Exception):
    pass


class PipelinesFileParser:
    def __init__(self, file_path: str):
        self._file_path = file_path

    def parse(self):
        if not os.path.isfile(self._file_path):
            raise ValueError(f"Pipelines file not found: {self._file_path}")

        with open(self._file_path) as f:
            pipelines_data = yaml.load(f, Loader=YamlLoader)

        pipelines = self._parse_pipelines(pipelines_data)
        caches, services = self._parse_definitions(pipelines_data)

        return Pipelines(None, pipelines, caches, services)

    def _parse_pipelines(self, data):
        if "pipelines" not in data:
            raise PipelineParseError("Invalid pipelines file: Key not found: 'pipelines'")

        pipeline_groups = data["pipelines"]

        group_names = set(pipeline_groups.keys())

        if not group_names:
            raise PipelineParseError("No pipeline groups")

        invalid_groups = group_names - {"branches", "custom"}
        if invalid_groups:
            raise PipelineParseError(f"Invalid groups: {invalid_groups}")

        pipelines = {}

        for g in group_names:
            for name, steps in pipeline_groups[g].items():
                path = f"{g}.{name}"
                pipelines[path] = Pipeline(path, name, self._parse_steps(steps))

        return pipelines

    def _parse_steps(self, step_list):
        steps = []
        for value in step_list:
            if "step" not in value:
                continue

            value = value["step"]

            image = value.get("image")
            if image:
                image = self._parse_image(image)

            steps.append(PipelineStep(value["name"], value["script"], image))

        return steps

    def _parse_image(self, value):
        if isinstance(value, str):
            return PipelineImage(value)

        name = value["name"]
        username = value.get("username")
        password = value.get("password")
        email = value.get("email")
        user = value.get("run-as-user")
        aws = value.get("aws")

        return PipelineImage(name, username, password, email, user, aws)

    def _parse_definitions(self, data):
        if "definitions" not in data:
            return None, None

        definitions = data["definitions"]
        caches = []
        services = []

        for name, path in definitions.get("caches", {}).items():
            caches.append(PipelineCache(name, path))

        for name, value in definitions.get("services", {}).items():
            image = value.get("image")
            environment = value.get("environment")
            memory = int(value["memory"]) if "memory" in value else None

            services.append(PipelineService(name, image, environment, memory))

        return caches, services


class PipelineRunner:
    def __init__(self, pipeline: str, env_files: List[str]):
        self._pipeline = pipeline
        self._env_files = env_files

    def run(self):
        pipelines_definition = PipelinesFileParser("bitbucket-pipelines.yml").parse()

        pipeline_to_run = pipelines_definition.get_pipeline(self._pipeline)

        if not pipeline_to_run:
            raise ValueError(f"Invalid pipeline: {self._pipeline}")

        print(dumps(pipeline_to_run))


@click.command("Pipeline Runner")
@click.argument("pipeline", required=True)
@click.option("-e", "--env-file", "env_files", multiple=True, help="Read in a file of environment variables")
def main(pipeline, env_files):
    """
    Runs the pipeline PIPELINE.

    PIPELINE is the full path to the pipeline to run. Ex: branches.master
    """

    PipelineRunner(pipeline, env_files).run()
