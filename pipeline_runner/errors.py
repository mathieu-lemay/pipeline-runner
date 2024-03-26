class InvalidPipelineError(ValueError):
    def __init__(self, pipeline_name: str) -> None:
        super().__init__(f"Invalid pipeline: {pipeline_name}")


class InvalidServiceError(ValueError):
    def __init__(self, service_name: str) -> None:
        super().__init__(f"Invalid service: {service_name}")


class NegativeIntegerError(ValueError):
    def __init__(self) -> None:
        super().__init__("value must be a positive integer")


class UnsupportedCacheError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Custom caches are not supported: {name}")
