import os.path

import yaml

from .models import PipelineSpec

try:
    from yaml import CLoader as YamlLoader
except ImportError:
    # noinspection PyUnresolvedReferences
    from yaml import YamlLoader


def parse_pipeline_file(file_path: str) -> PipelineSpec:
    if not os.path.isfile(file_path):
        raise ValueError(f"Pipelines file not found: {file_path}")

    with open(file_path) as f:
        pipelines_data = yaml.load(f, Loader=YamlLoader)

    return PipelineSpec.parse_obj(pipelines_data)
