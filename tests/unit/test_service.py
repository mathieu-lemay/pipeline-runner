import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

from pipeline_runner.models import Service
from pipeline_runner.service import DockerServiceRunner


def test_docker_service_mounts_daemon_config(tmp_path: Path) -> None:
    client = MagicMock()
    client.volumes.list.return_value = []
    client.volumes.create.return_value.name = "cache-volume"

    step_ctx = Mock()
    step_ctx.pipeline_ctx.get_pipeline_data_directory.return_value = str(tmp_path)

    runner = DockerServiceRunner(
        client,
        step_ctx,
        "docker",
        Service(memory=1024),
        "container:some-pause-container",
        "shared-volume",
        "project",
        "/cache",
    )

    volumes = runner._get_volumes()

    daemon_config_file = tmp_path / "project_docker_service-daemon.json"
    assert volumes[str(daemon_config_file)] == {"bind": "/etc/docker/daemon.json", "mode": "ro"}
    assert json.loads(daemon_config_file.read_text()) == {
        "bip": "172.31.160.1/24",
        "default-address-pools": [{"base": "172.31.161.0/24", "size": 27}],
    }
