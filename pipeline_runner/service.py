import logging
from importlib.resources import as_file, files
from typing import cast

import docker  # type: ignore[import-untyped]
from docker import DockerClient
from docker.models.containers import Container  # type: ignore[import-untyped]
from docker.models.volumes import Volume  # type: ignore[import-untyped]
from slugify import slugify
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

import pipeline_runner

from .config import config
from .container import ContainerScriptRunner, pull_image
from .context import StepRunContext
from .errors import InvalidServiceError
from .models import Service

logger = logging.getLogger(__name__)


class ServiceNotReadyError(Exception):
    pass


class ServiceUnhealthyError(Exception):
    pass


class ServicesManager:
    def __init__(
        self,
        ctx: StepRunContext,
        shared_data_volume_name: str,
    ) -> None:
        self._ctx = ctx

        service_names = ctx.step.services
        service_definitions = ctx.pipeline_ctx.services
        self._services_by_name = self._get_services(service_names, service_definitions)

        self._memory_multiplier = ctx.step.size.as_int()
        self._shared_data_volume_name = shared_data_volume_name
        self._repository_slug = ctx.pipeline_ctx.project_metadata.path_slug
        self._pipeline_cache_directory = ctx.pipeline_ctx.get_cache_directory()

        self._client = docker.from_env()

        self._service_runners: dict[str, ServiceRunner] = {}

    def start_services(self, network_name: str) -> None:
        self._ensure_memory_for_services()

        for service_name, service in self._services_by_name.items():
            sr = ServiceRunnerFactory.get(
                self._client,
                self._ctx,
                service_name,
                service,
                network_name,
                self._shared_data_volume_name,
                self._repository_slug,
                self._pipeline_cache_directory,
            )
            sr.start()
            self._service_runners[sr.slug] = sr

    def stop_services(self) -> None:
        for s, sr in self._service_runners.items():
            try:
                sr.stop()
            except Exception:  # noqa: PERF203  # try-except within a loop incurs performance overhead
                logger.exception("Error removing service '%s'", s)

    def get_services_containers(self) -> dict[str, Container]:
        return {name: runner.container for name, runner in self._service_runners.items()}

    def get_memory_usage(self) -> int:
        return sum(s.memory for s in self._services_by_name.values())

    @staticmethod
    def _get_services(service_names: list[str], service_definitions: dict[str, Service]) -> dict[str, Service]:
        services = {}
        for service_name in service_names:
            if service_name not in service_definitions:
                raise InvalidServiceError(service_name)

            services[service_name] = service_definitions[service_name]

        return services

    def _ensure_memory_for_services(self) -> None:
        requested_mem = self.get_memory_usage()
        available_mem = self._get_service_containers_memory_limit()
        if requested_mem > available_mem:
            msg = (
                f"Not enough memory to run all services. Requested: {requested_mem}MiB / Available: {available_mem}MiB"
            )
            raise ValueError(msg)

    def _get_service_containers_memory_limit(self) -> int:
        return config.total_memory_limit * self._memory_multiplier - config.build_container_minimum_memory


class ServiceRunner:
    def __init__(
        self,
        docker_client: DockerClient,
        step_ctx: StepRunContext,
        service_name: str,
        service: Service,
        network_name: str,
        shared_data_volume_name: str,
        project_slug: str,
        pipeline_cache_directory: str,
    ) -> None:
        self._client = docker_client
        self._step_ctx = step_ctx
        self._service_name = service_name
        self._service = service
        self._network_name = network_name
        self._shared_data_volume_name = shared_data_volume_name
        self._project_slug = project_slug
        self._pipeline_cache_directory = pipeline_cache_directory
        self._container = None

        self._slug = slugify(self._service_name)

    @property
    def slug(self) -> str:
        return self._slug

    @property
    def container(self) -> Container:
        return self._container

    def start(self) -> None:
        if not self._service.image:
            msg = "Service has no image."
            raise ValueError(msg)

        logger.info("Starting service: %s", self._service_name)
        pull_image(self._client, self._step_ctx, self._service.image)

        self._container = self._start_container()

        logger.info("Waiting for service to be ready: %s", self._service_name)
        self._ensure_container_ready(self._container)

    def _start_container(self) -> Container:
        if not self._service.image:
            msg = "Service has no image."
            raise ValueError(msg)

        return self._client.containers.run(
            self._service.image.name,
            name=self._get_container_name(),
            environment=self._service.variables,
            network=self._network_name,
            mem_limit=self._get_mem_limit(),
            detach=True,
        )

    def _ensure_container_ready(self, container: Container) -> None:
        pass

    def _get_container_name(self) -> str:
        return f"{self._project_slug}-service-{self._slug}"

    def stop(self) -> None:
        if not self._container:
            # TODO: Refactor to remove illegal state.
            raise Exception("called on uninitialized service")

        logger.info("Removing service: %s", self._service_name)

        self._teardown()

        self._container.remove(v=True, force=True)

    def _get_mem_limit(self) -> int:
        return self._service.memory * 2**20

    def _teardown(self) -> None:
        pass


class DockerServiceRunner(ServiceRunner):
    def _start_container(self) -> Container:
        if not self._service.image:
            raise ValueError("Service has no image.")

        environment = self._service.variables
        environment["DOCKER_TLS_CERTDIR"] = ""

        return self._client.containers.run(
            self._service.image.name,
            name=self._get_container_name(),
            command=["--tls=false"],
            environment=environment,
            network=self._network_name,
            privileged=True,
            volumes=self._get_volumes(),
            mem_limit=self._get_mem_limit(),
            detach=True,
            healthcheck={
                "start_period": 30_000_000_000,
                "timeout": 1_000_000_000,
            },
        )

    @retry(wait=wait_fixed(1), stop=stop_after_delay(30), retry=retry_if_exception_type(ServiceNotReadyError))
    def _ensure_container_ready(self, container: Container) -> None:
        # Refresh container to ensure we have its health status
        container = self._client.containers.get(container.name)

        match container.health:
            case "healthy":
                return
            case "unhealthy":
                raise ServiceUnhealthyError
            case "unknown":
                # Fallback to non-heathcheck running check
                pass
            case _:
                raise ServiceNotReadyError

        if container.status != "running":
            logger.debug("Container is %s", container.status)
            raise ServiceNotReadyError

        # Typing is not strictly true, values are list[list[Any]]
        # But the last element is a str and that's the only one we care about
        processes = cast("dict[str, list[list[str]]]", container.top())

        # Ensure that docker is running in the container before attempting an exec_run
        if not any(p for p in processes["Processes"] if p[-1].startswith("dockerd")):
            raise ServiceNotReadyError

        result = container.exec_run(("docker", "info"))
        if result.exit_code != 0:
            logger.debug("docker info result: exit_code=%d, output=%s", result.exit_code, result.output.decode())
            raise ServiceNotReadyError

    def _get_volumes(self) -> dict[str, dict[str, str]]:
        static_files = files(pipeline_runner).joinpath("static")

        with as_file(static_files.joinpath("runit.sh")) as p:
            runit_script_path = p

        cache_volume = self._get_cache_volume()

        return {
            cache_volume.name: {"bind": "/var/lib/docker"},
            self._shared_data_volume_name: {"bind": config.remote_pipeline_dir},
            str(runit_script_path): {"bind": "/usr/local/bin/runit.sh"},
        }

    def _get_cache_volume(self) -> Volume:
        label_name = "org.acidrain.pipeline_runner.project"
        label_value = self._project_slug

        volumes = self._client.volumes.list(filters={"label": f"{label_name}={label_value}"})

        if not volumes:
            volume = self._client.volumes.create(
                f"{self._get_container_name()}-cache", labels={label_name: label_value}
            )
        elif len(volumes) == 1:
            volume = volumes[0]
        else:
            raise Exception("Found more than one cache volume")

        return volume

    def _teardown(self) -> None:
        logger.info("Executing teardown for service: %s", self._service_name)

        script = [
            "docker ps -q | xargs -r docker kill",
            "docker container prune -f",
            "docker volume prune -f",
        ]
        csr = ContainerScriptRunner(self._container, script)
        csr.run()


class ServiceRunnerFactory:
    @staticmethod
    def get(
        docker_client: DockerClient,
        step_ctx: StepRunContext,
        service_name: str,
        service_def: Service,
        network_name: str,
        shared_data_volume_name: str,
        repository_slug: str,
        pipeline_cache_directory: str,
    ) -> ServiceRunner:
        cls: type[ServiceRunner | DockerServiceRunner]

        cls = DockerServiceRunner if service_name == "docker" else ServiceRunner

        return cls(
            docker_client,
            step_ctx,
            service_name,
            service_def,
            network_name,
            shared_data_volume_name,
            repository_slug,
            pipeline_cache_directory,
        )
