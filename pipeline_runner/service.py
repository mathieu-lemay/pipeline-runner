import logging
import os
from typing import Dict, List

import docker
from slugify import slugify

from . import utils
from .config import config
from .container import ContainerScriptRunner, pull_image
from .models import Service

logger = logging.getLogger(__name__)


class ServicesManager:
    def __init__(self, service_names: List[str], service_definitions: Dict[str, Service], memory_multiplier: int):
        self._service_names = service_names
        self._service_definitions = service_definitions
        self._memory_multiplier = memory_multiplier

        self._client = docker.from_env()
        self._privileged_services = ("docker",)
        self._teardowns = {"docker": self._teardown_docker}

        self._containers = {}

    def start_services(self):
        requested_services = [self._get_service_definition(s) for s in self._service_names]
        self._ensure_memory_for_services(requested_services)

        for service in requested_services:
            self._start_service(service)

    def stop_services(self):
        for s, c in self._containers.items():
            try:
                logger.info("Removing service: %s", s)

                teardown = self._teardowns.get(s)
                if teardown:
                    logger.info("Executing teardown for service: %s", s)
                    teardown(c)

                c.remove(v=True, force=True)
            except Exception as e:
                logger.exception("Error removing service '%s': %s", s, e)

    def get_container_links(self):
        return {c.name: s for s, c in self._containers.items()}

    def _start_service(self, service: Service):
        logger.info("Starting service: %s", service.name)
        pull_image(self._client, service.image)

        service_name_slug = slugify(service.name)

        name = f"{config.project_slug}-service-{service_name_slug}"

        container = self._client.containers.run(
            service.image.name,
            name=name,
            command=service.command,
            environment=service.environment,
            hostname=service_name_slug,
            network_mode="host",
            privileged=self._is_privileged(service.name),
            volumes=self._get_volumes(service.name),
            mem_limit=service.memory * 2 ** 20,
            detach=True,
        )

        self._containers[service_name_slug] = container

    def _get_service_definition(self, service_name):
        if service_name not in self._service_definitions:
            raise ValueError(f"Invalid service: {service_name}")

        return self._service_definitions[service_name]

    def _ensure_memory_for_services(self, services):
        requested_mem = sum(s.memory for s in services)
        available_mem = self._get_service_containers_memory_limit()
        if requested_mem > available_mem:
            raise ValueError(
                f"Not enough memory to run all services. Requested: {requested_mem}MiB / Available: {available_mem}MiB"
            )

    def _get_service_containers_memory_limit(self) -> int:
        return config.service_containers_base_memory_limit * self._memory_multiplier

    def _is_privileged(self, name):
        return name in self._privileged_services

    @staticmethod
    def _get_volumes(name):
        if name == "docker":
            return {
                os.path.join(utils.get_local_cache_directory(), "docker"): {"bind": "/var/lib/docker"},
                "PipelineDataVolume": {"bind": config.remote_pipeline_dir},
            }
        return None

    @staticmethod
    def _teardown_docker(container):
        script = "\n".join(
            [
                'containers="$(docker ps -q)"',
                'if [ -n "${containers}" ]; then',
                "    docker kill ${containers}",
                "fi",
                "docker container prune -f",
                "docker image prune -f",
                "docker volume prune -f",
            ]
        )
        csr = ContainerScriptRunner(container, script)
        csr.run()
