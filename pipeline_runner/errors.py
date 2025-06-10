from textwrap import indent

from click.exceptions import UsageError


class PipelinesFileNotFoundError(UsageError, ValueError):
    def __init__(self, file: str) -> None:
        super().__init__(f"Pipelines file not found: {file}")


class PipelinesFileParseError(UsageError):
    def __init__(self, error: str) -> None:
        super().__init__(f"Error parsing pipelines file:\n{error}")


class PipelinesFileValidationError(UsageError):
    def __init__(self, error: str) -> None:
        super().__init__(f"Invalid pipelines file:\n{indent(error, '  ')}")


class InvalidPipelineError(UsageError, ValueError):
    def __init__(self, pipeline_name: str, valid_pipelines: list[str] | None = None) -> None:
        msg = f"Invalid pipeline: {pipeline_name}"

        if valid_pipelines:
            valid_pipelines_str = "\n\t".join(valid_pipelines)
            msg += f"\nAvailable pipelines:\n\t{valid_pipelines_str}"

        super().__init__(msg)


class InvalidServiceError(UsageError, ValueError):
    def __init__(self, service_name: str) -> None:
        super().__init__(f"Invalid service: {service_name}")


class NegativeIntegerError(ValueError):
    def __init__(self) -> None:
        super().__init__("value must be a positive integer")


class InvalidCacheKeyError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f'Cache "{name}": Cache key files could not be found')


class ArtifactManagementError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)
