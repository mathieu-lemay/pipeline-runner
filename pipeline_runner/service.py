import logging
import os
from typing import Dict, List, Optional

import docker
from docker import DockerClient
from docker.models.containers import Container
from slugify import slugify

from . import utils
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
    ):
        self._services = self._get_services(service_names, service_definitions)
        self._memory_multiplier = memory_multiplier
        self._shared_data_volume_name = shared_data_volume_name

        self._client = docker.from_env()

        self._containers = {}

    def start_services(self):
        self._ensure_memory_for_services()

        for service in self._services:
            sr = ServiceRunnerFactory.get(self._client, service, self._shared_data_volume_name)
            sr.start()
            self._containers[sr.slug] = sr

    def stop_services(self):
        for s, sr in self._containers.items():
            try:
                sr.stop()
            except Exception as e:
                logger.exception("Error removing service '%s': %s", s, e)

    def get_services_names(self) -> List[str]:
        return list(self._containers.keys())

    def get_memory_usage(self) -> int:
        return sum(s.memory for s in self._services)

    @staticmethod
    def _get_services(service_names, service_definitions) -> [Service]:
        services = []
        for service_name in service_names:
            if service_name not in service_definitions:
                raise ValueError(f"Invalid service: {service_name}")

            services.append(service_definitions[service_name])

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
    def __init__(self, docker_client: DockerClient, service: Service, shared_data_volume_name: str):
        self._client = docker_client
        self._service = service
        self._shared_data_volume_name = shared_data_volume_name
        self._container = None

        self._slug = slugify(self._service.name)

    @property
    def slug(self):
        return self._slug

    def start(self):
        logger.info("Starting service: %s", self._service.name)
        pull_image(self._client, self._service.image)

        self._container = self._start_container()

    def _start_container(self) -> Container:
        name = self._get_container_name()

        container = self._client.containers.run(
            self._service.image.name,
            name=name,
            command=self._service.command,
            environment=self._service.environment,
            hostname=self._slug,
            network_mode="host",
            mem_limit=self._get_mem_limit(),
            detach=True,
        )

        return container

    def _get_container_name(self):
        return f"{config.project_slug}-service-{self._slug}"

    def stop(self):
        logger.info("Removing service: %s", self._service.name)

        self._teardown()

        self._container.remove(v=True, force=True)

    def _get_mem_limit(self) -> int:
        return self._service.memory * 2 ** 20

    def _teardown(self):
        pass


class DockerServiceRunner(ServiceRunner):
    def _start_container(self) -> Container:
        name = self._get_container_name()

        container = self._client.containers.run(
            self._service.image.name,
            name=name,
            command=self._service.command,
            environment=self._service.environment,
            hostname=self._slug,
            network_mode="host",
            privileged=True,
            volumes=self._get_volumes(),
            mem_limit=self._get_mem_limit(),
            detach=True,
        )

        return container

    def _get_volumes(self) -> Optional[Dict[str, Dict[str, str]]]:
        return {
            os.path.join(utils.get_local_cache_directory(), "docker"): {"bind": "/var/lib/docker"},
            self._shared_data_volume_name: {"bind": config.remote_pipeline_dir},
        }

    def _teardown(self):
        logger.info("Executing teardown for service: %s", self._service.name)

        script = "\n".join(
            [
                'containers="$(docker ps -q)"',
                'if [ -n "${containers}" ]; then',
                "    docker kill ${containers}",
                "fi",
                "docker container prune -f",
                "docker volume prune -f",
            ]
        )
        csr = ContainerScriptRunner(self._container, script)
        csr.run()


class ServiceRunnerFactory:
    @staticmethod
    def get(docker_client: DockerClient, service: Service, shared_data_volume_name: str) -> ServiceRunner:
        if service.name == "docker":
            cls = DockerServiceRunner
        else:
            cls = ServiceRunner

        return cls(docker_client, service, shared_data_volume_name)
