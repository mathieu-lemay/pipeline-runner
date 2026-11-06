import json
from ipaddress import IPv4Network
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from pipeline_runner.config import Config, DockerDaemonSettings
from pipeline_runner.models import Service
from pipeline_runner.service import DockerServiceRunner


@pytest.fixture
def docker_service_runner(tmp_path: Path) -> DockerServiceRunner:
    client = MagicMock()
    client.volumes.list.return_value = []
    client.volumes.create.return_value.name = "cache-volume"

    step_ctx = Mock()
    step_ctx.pipeline_ctx.get_pipeline_data_directory.return_value = str(tmp_path)

    return DockerServiceRunner(
        client,
        step_ctx,
        "docker",
        Service(memory=1024),
        "container:some-pause-container",
        "shared-volume",
        "project",
        "/cache",
    )


def test_docker_service_mounts_daemon_config(
    docker_service_runner: DockerServiceRunner, config: Config, tmp_path: Path
) -> None:
    config.docker_daemon = DockerDaemonSettings(
        bip="172.31.160.1/24",
        dapbase=IPv4Network("172.31.161.0/24"),
        dapsize=27,
    )

    volumes = docker_service_runner._get_volumes()

    daemon_config_file = tmp_path / "project_docker_service-daemon.json"
    assert volumes[str(daemon_config_file)] == {"bind": "/etc/docker/daemon.json", "mode": "ro"}
    assert json.loads(daemon_config_file.read_text()) == {
        "bip": "172.31.160.1/24",
        "default-address-pools": [{"base": "172.31.161.0/24", "size": 27}],
    }


def test_docker_service_without_daemon_config_does_not_mount_it(
    docker_service_runner: DockerServiceRunner, config: Config, tmp_path: Path
) -> None:
    config.docker_daemon = None

    volumes = docker_service_runner._get_volumes()

    assert not any(v["bind"] == "/etc/docker/daemon.json" for v in volumes.values())
    assert not (tmp_path / "project_docker_service-daemon.json").exists()
