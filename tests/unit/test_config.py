import os
from ipaddress import IPv4Network

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from pipeline_runner.config import (
    Config,
    DockerDaemonSettings,
)


@pytest.mark.parametrize(
    ("volume", "expected_error"),
    [
        ("~/.cache", None),
        ("~/.cache:/cache", None),
        ("~/.cache:/cache:ro", None),
        ("", "empty volume spec"),
        ("~/.cache:/cache:ro:wat", "invalid volume spec"),
    ],
)
def test_validate_volumes_conform_to_docker_spec(*, volume: str, expected_error: str | None) -> None:
    if expected_error:
        with pytest.raises(ValidationError, match=expected_error):
            Config.model_validate({"volumes": [volume]})
    else:
        Config.model_validate({"volumes": [volume]})


def test_validate_validates_all_volumes() -> None:
    volumes = ["", "foo:x:y:z", "valid", "bar:x:y:z"]

    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate({"volumes": volumes})

    assert "Invalid volume: : empty volume spec" in str(exc_info.value)
    assert "Invalid volume: foo:x:y:z: invalid volume spec" in str(exc_info.value)
    assert "Invalid volume: bar:x:y:z: invalid volume spec" in str(exc_info.value)


def test_docker_daemon_settings_to_config_file() -> None:
    settings = DockerDaemonSettings(bip="172.31.160.1/24", dapbase=IPv4Network("172.31.161.0/24"), dapsize=27)
    assert (
        settings.to_config_file(indent=None)
        == b'{"bip":"172.31.160.1/24","default-address-pools":[{"base":"172.31.161.0/24","size":27}]}'
    )


def test_docker_daemon_settings_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_RUNNER_DOCKER_DAEMON_BIP", "172.31.160.1/24")
    monkeypatch.setenv("PIPELINE_RUNNER_DOCKER_DAEMON_DAPBASE", "172.31.161.0/24")
    monkeypatch.setenv("PIPELINE_RUNNER_DOCKER_DAEMON_DAPSIZE", "27")

    assert Config().docker_daemon == DockerDaemonSettings(
        bip="172.31.160.1/24", dapbase=IPv4Network("172.31.161.0/24"), dapsize=27
    )


def test_docker_daemon_settings_default_to_none() -> None:
    assert Config().docker_daemon is None


@pytest.mark.parametrize(
    ("volume", "expected"),
    [
        ("~/.cache:~:~", f"{os.path.expanduser('~')}/.cache:~:~"),
        ("${SOMEVAR}:${SOMEVAR}:${SOMEVAR}", "expanded-var:${SOMEVAR}:${SOMEVAR}"),
        (
            "~/${SOMEVAR}:~/${SOMEVAR}:~/${SOMEVAR}",
            f"{os.path.expanduser('~')}/expanded-var:~/${{SOMEVAR}}:~/${{SOMEVAR}}",
        ),
    ],
)
def test_validate_expands_first_part(monkeypatch: MonkeyPatch, *, volume: str, expected: str) -> None:
    monkeypatch.setenv("SOMEVAR", "expanded-var")

    c = Config.model_validate({"volumes": [volume]})

    assert c.volumes == [expected]
