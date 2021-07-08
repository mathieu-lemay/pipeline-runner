import logging
import os
from typing import Dict, List, Optional

import docker
from docker import DockerClient
from docker.models.containers import Container
from slugify import slugify

from .config import config
from .container import ContainerScriptRunner, pull_image
from .models import Service

logger = logging.getLogger(__name__)


class ServicesManager:
    def __init__(
        self,
        service_names: List[str],
        service_definitions: Dict[str, Service],
        memory_multiplier: int,
        shared_data_volume_name: str,
        repository_slug: str,
        pipeline_cache_directory: str,
    ):
        self._services_by_name = self._get_services(service_names, service_definitions)
        self._memory_multiplier = memory_multiplier
        self._shared_data_volume_name = shared_data_volume_name
        self._repository_slug = repository_slug
        self._pipeline_cache_directory = pipeline_cache_directory

        self._client = docker.from_env()

        self._service_runners = {}

    def start_services(self, network_name: str):
        self._ensure_memory_for_services()

        for service_name, service in self._services_by_name.items():
            sr = ServiceRunnerFactory.get(
                self._client,
                service_name,
                service,
                network_name,
                self._shared_data_volume_name,
                self._repository_slug,
                self._pipeline_cache_directory,
            )
            sr.start()
            self._service_runners[sr.slug] = sr

    def stop_services(self):
        for s, sr in self._service_runners.items():
            try:
                sr.stop()
            except Exception as e:
                logger.exception("Error removing service '%s': %s", s, e)

    def get_services_containers(self) -> Dict[str, Container]:
        return {name: runner.container for name, runner in self._service_runners.items()}

    def get_memory_usage(self) -> int:
        return sum(s.memory for s in self._services_by_name.values())

    @staticmethod
    def _get_services(service_names, service_definitions) -> Dict[str, Service]:
        services = {}
        for service_name in service_names:
            if service_name not in service_definitions:
                raise ValueError(f"Invalid service: {service_name}")

            services[service_name] = service_definitions[service_name]

        return services

    def _ensure_memory_for_services(self):
        requested_mem = self.get_memory_usage()
        available_mem = self._get_service_containers_memory_limit()
        if requested_mem > available_mem:
            raise ValueError(
                f"Not enough memory to run all services. Requested: {requested_mem}MiB / Available: {available_mem}MiB"
            )

    def _get_service_containers_memory_limit(self) -> int:
        return config.total_memory_limit * self._memory_multiplier - config.build_container_minimum_memory


class ServiceRunner:
    def __init__(
        self,
        docker_client: DockerClient,
        service_name: str,
        service: Service,
        network_name: str,
        shared_data_volume_name: str,
        project_slug: str,
        pipeline_cache_directory: str,
    ):
        self._client = docker_client
        self._service_name = service_name
        self._service = service
        self._network_name = network_name
        self._shared_data_volume_name = shared_data_volume_name
        self._project_slug = project_slug
        self._pipeline_cache_directory = pipeline_cache_directory
        self._container = None

        self._slug = slugify(self._service_name)

    @property
    def slug(self):
        return self._slug

    @property
    def container(self):
        return self._container

    def start(self):
        logger.info("Starting service: %s", self._service_name)
        pull_image(self._client, self._service.image)

        self._container = self._start_container()

    def _start_container(self) -> Container:
        container = self._client.containers.run(
            self._service.image.name,
            name=self._get_container_name(),
            environment=self._service.variables,
            network=self._network_name,
            mem_limit=self._get_mem_limit(),
            detach=True,
        )

        return container

    def _get_container_name(self):
        return f"{self._project_slug}-service-{self._slug}"

    def stop(self):
        logger.info("Removing service: %s", self._service_name)

        self._teardown()

        self._container.remove(v=True, force=True)

    def _get_mem_limit(self) -> int:
        return self._service.memory * 2 ** 20

    def _teardown(self):
        pass


class DockerServiceRunner(ServiceRunner):
    def _start_container(self) -> Container:
        environment = self._service.variables
        environment["DOCKER_TLS_CERTDIR"] = ""

        container = self._client.containers.run(
            self._service.image.name,
            name=self._get_container_name(),
            command="--tls=false",
            environment=environment,
            network=self._network_name,
            privileged=True,
            volumes=self._get_volumes(),
            mem_limit=self._get_mem_limit(),
            detach=True,
        )

        return container

    def _get_volumes(self) -> Optional[Dict[str, Dict[str, str]]]:
        return {
            os.path.join(self._pipeline_cache_directory, "docker"): {"bind": "/var/lib/docker"},
            self._shared_data_volume_name: {"bind": config.remote_pipeline_dir},
        }

    def _teardown(self):
        logger.info("Executing teardown for service: %s", self._service_name)

        script = [
            'containers="$(docker ps -q)"',
            'if [ -n "${containers}" ]; then',
            "    docker kill ${containers}",
            "fi",
            "docker container prune -f",
            "docker volume prune -f",
        ]
        csr = ContainerScriptRunner(self._container, script)
        csr.run()


class ServiceRunnerFactory:
    @staticmethod
    def get(
        docker_client: DockerClient,
        service_name: str,
        service_def: Service,
        network_name: str,
        shared_data_volume_name: str,
        repository_slug: str,
        pipeline_cache_directory: str,
    ) -> ServiceRunner:
        if service_name == "docker":
            cls = DockerServiceRunner
        else:
            cls = ServiceRunner

        return cls(
            docker_client,
            service_name,
            service_def,
            network_name,
            shared_data_volume_name,
            repository_slug,
            pipeline_cache_directory,
        )
