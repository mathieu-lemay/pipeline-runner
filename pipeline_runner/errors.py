class InvalidPipelineError(ValueError):
    def __init__(self, pipeline_name: str) -> None:
        super().__init__(f"Invalid pipeline: {pipeline_name}")


class InvalidServiceError(ValueError):
    def __init__(self, service_name: str) -> None:
        super().__init__(f"Invalid service: {service_name}")


class NegativeIntegerError(ValueError):
    def __init__(self) -> None:
        super().__init__("value must be a positive integer")


class InvalidCacheKeyError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f'Cache "{name}": Cache key files could not be found')
