import shlex
import subprocess
from pathlib import Path

import pytest

from pipeline_runner.artifacts import ArtifactManager
from pipeline_runner.models import ArtifactDefinition


def test_find_args_combine_paths_ignore_paths_and_depth() -> None:
    definition = ArtifactDefinition(
        name="reports", paths=["reports/**", "*.log"], ignore_paths=["reports/*.html"], depth=2
    )

    args = ArtifactManager._build_find_args(definition)

    assert args == [
        "find",
        "-maxdepth",
        "2",
        "-type",
        "f",
        "(",
        "-path",
        "./reports/**",
        "-o",
        "-path",
        "./*.log",
        ")",
        "!",
        "(",
        "-path",
        "./reports/*.html",
        ")",
    ]


@pytest.mark.parametrize(
    "glob",
    [
        "x'; touch pwned; echo '",
        "$(touch pwned)",
        "`touch pwned`",
        "a b; touch pwned",
        "x | touch pwned",
    ],
)
def test_prepare_command_does_not_allow_shell_injection(glob: str, tmp_path: Path) -> None:
    definition = ArtifactDefinition(name="evil", paths=[glob], ignore_paths=[glob])

    cmd = ArtifactManager._build_prepare_command(definition, "out.tar")
    find_part = shlex.join(ArtifactManager._build_find_args(definition))

    assert cmd.startswith(f"{find_part} | tar ")
    # The glob survives as a single, literal argument.
    assert f"./{glob}" in shlex.split(find_part)

    # And running the find part through a real shell does not execute anything.
    subprocess.run(["/bin/sh", "-c", find_part], cwd=tmp_path, check=True, capture_output=True)  # noqa: S603
    assert not (tmp_path / "pwned").exists()
