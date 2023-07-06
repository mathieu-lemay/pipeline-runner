import os.path

import yaml

from .models import PipelineSpec


def parse_pipeline_file(file_path: str) -> PipelineSpec:
    if not os.path.isfile(file_path):
        raise ValueError(f"Pipelines file not found: {file_path}")

    with open(file_path) as f:
        pipelines_data = yaml.safe_load(f)

    return PipelineSpec.model_validate(pipelines_data)
