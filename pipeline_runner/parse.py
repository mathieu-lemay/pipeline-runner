import os.path

import yaml
from pydantic import ValidationError
from yaml.parser import ParserError

from .errors import PipelinesFileNotFoundError, PipelinesFileParseError, PipelinesFileValidationError
from .models import PipelineSpec


def parse_pipeline_file(file_path: str) -> PipelineSpec:
    if not os.path.isfile(file_path):
        raise PipelinesFileNotFoundError(file_path)

    try:
        with open(file_path) as f:
            pipelines_data = yaml.safe_load(f)

        return PipelineSpec.model_validate(pipelines_data)
    except ParserError as e:
        raise PipelinesFileParseError(str(e)) from e
    except ValidationError as e:
        raise PipelinesFileValidationError(str(e)) from e
