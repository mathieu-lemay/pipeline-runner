import os
import re
from pathlib import Path

import pkg_resources
from click.testing import CliRunner

from pipeline_runner.cli import main


def test_specifying_no_command_shows_help() -> None:
    runner = CliRunner()
    # noinspection PyTypeChecker
    result = runner.invoke(main)

    assert result.exit_code == 1
    assert result.output == main.make_context("Pipeline Runner", []).get_help() + "\n"


def test_show_version() -> None:
    runner = CliRunner()
    # noinspection PyTypeChecker
    result = runner.invoke(main, ["--version"])

    expected = f"Pipeline Runner {pkg_resources.get_distribution('bitbucket_pipeline_runner').version}\n"
    assert result.exit_code == 0
    assert result.output == expected


def test_list_pipelines(tmp_path_chdir: Path) -> None:
    with open(os.path.join(tmp_path_chdir, "bitbucket-pipelines.yml"), "w") as f:
        f.write(
            """pipelines:
  default:
    - step:
        script:
          - "true"

  custom:
    some-pipeline:
      - step:
          script:
            - "true"

    another-pipeline:
      - step:
          script:
            - "true"

    yet-another-pipeline:
      - step:
          script:
            - "true"

  branches:
    master:
      - step:
          script:
            - "true"

    develop:
      - step:
          script:
            - "true"

    feature/**:
      - step:
          script:
            - "true"
"""
        )

    runner = CliRunner()

    # noinspection PyTypeChecker
    result = runner.invoke(main, ["list"], env={"NO_COLOR": ""})

    assert result.exit_code == 0

    output_lines = result.output.split("\n")
    log_ts = re.match("[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}.[0-9]{3}", output_lines[0])
    assert log_ts is not None, "Unable to find timestamp in logs"

    expected = [
        f"{log_ts.group()} [INFO    ] pipeline_runner.cli: Available pipelines:",
        "\tbranches.develop",
        "\tbranches.feature/**",
        "\tbranches.master",
        "\tcustom.another-pipeline",
        "\tcustom.some-pipeline",
        "\tcustom.yet-another-pipeline",
        "\tdefault",
        "",
    ]

    assert output_lines == expected
