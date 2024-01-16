import logging
import logging.config
import os
import shutil
import sys
from importlib.metadata import version

import click
from pyfzf import FzfPrompt  # type: ignore[import-untyped]

from pipeline_runner.errors import InvalidPipelineError

from . import utils
from .config import config
from .parse import parse_pipeline_file
from .runner import PipelineRunner, PipelineRunRequest

logger = logging.getLogger(__name__)


def _init_logger() -> None:
    logging.config.dictConfig(config.log_config)


def _get_pipelines_list(pipeline_file: str) -> list[str]:
    pipelines_definition = parse_pipeline_file(pipeline_file)

    return pipelines_definition.get_available_pipelines()


def _prompt_for_pipeline(pipeline_file: str) -> str | None:
    pipeline = None
    pipelines = _get_pipelines_list(pipeline_file)

    try:
        fzf = FzfPrompt()
        pipeline = next(iter(fzf.prompt(pipelines)), None)
        if not pipeline:
            logger.warning("No pipeline selected")
    except SystemError:
        logger.warning("fzf executable not found, disabling interactive pipeline selection.")

    return pipeline


@click.group("Pipeline Runner", invoke_without_command=True)
@click.option(
    "-V",
    "--version",
    "show_version",
    is_flag=True,
    help="Print project version and exit.",
)
@click.pass_context
def main(ctx: click.Context, *, show_version: bool) -> None:
    if show_version:
        click.echo(f"Pipeline Runner {version('bitbucket_pipeline_runner')}")
        ctx.exit()

    if not ctx.invoked_subcommand:
        click.echo(ctx.get_help())
        ctx.exit(1)


@main.command()
@click.argument("pipeline", required=False)
@click.option(
    "-r",
    "--repository-path",
    help="Path to the git repository. Defaults to current directory.",
)
@click.option(
    "-s",
    "--step",
    "steps",
    multiple=True,
    help="Steps to run. If none are specified, they will all be run. Can be specified multiple times.",
)
@click.option(
    "-e",
    "--env-file",
    "env_files",
    multiple=True,
    help="Read in a file of environment variables. Can be specified multiple times.",
)
@click.option(
    "-c",
    "--color/--no-color",
    default=True,
    help="Enable colored output. Default: True",
)
@click.option(
    "--cpu-limits/--no-cpu-limits",
    default=False,
    help="Enable to enforce cpu limits for the runner. Default: False",
)
@click.option(
    "--ssh/--no-ssh",
    "expose_ssh_agent",
    default=False,
    help="Expose the local ssh agent to the container. Default: False",
)
def run(
    pipeline: str | None,
    repository_path: str,
    steps: list[str],
    env_files: list[str],
    *,
    color: bool,
    cpu_limits: bool,
    expose_ssh_agent: bool,
) -> None:
    """
    Runs the pipeline PIPELINE.

    PIPELINE is the full path to the pipeline to run. Ex: branches.master
    """
    config.color = color
    config.cpu_limits = cpu_limits
    config.expose_ssh_agent = expose_ssh_agent

    _init_logger()

    if not pipeline:
        pipeline = _prompt_for_pipeline(os.path.join(repository_path or ".", "bitbucket-pipelines.yml"))

    if not pipeline:
        logger.error("pipeline not specified")
        sys.exit(2)

    req = PipelineRunRequest(pipeline, repository_path, steps, env_files)

    runner = PipelineRunner(req)
    try:
        runner.run()
    except Exception:
        logger.exception("Error running pipeline")
        sys.exit(1)


@main.command("list")
@click.option(
    "-r",
    "--repository-path",
    help="Path to the git repository. Defaults to current directory.",
)
@click.option(
    "-c",
    "--color/--no-color",
    default=True,
    help="Enable colored output",
)
def list_(repository_path: str, *, color: bool) -> None:
    """
    List the available pipelines.
    """
    config.color = color

    _init_logger()

    pipelines = _get_pipelines_list(os.path.join(repository_path or ".", "bitbucket-pipelines.yml"))

    logger.info("Available pipelines:\n\t%s", "\n\t".join(sorted(pipelines)))


@main.command()
@click.argument("pipeline", required=False)
@click.option(
    "-r",
    "--repository-path",
    help="Path to the git repository. Defaults to current directory.",
)
def parse(pipeline: str | None, repository_path: str) -> None:
    """
    Parse the pipeline file.
    """
    pipeline_file = os.path.join(repository_path or ".", "bitbucket-pipelines.yml")

    pipelines_definition = parse_pipeline_file(pipeline_file)

    if pipeline:
        parsed = pipelines_definition.get_pipeline(pipeline)
        if not parsed:
            raise InvalidPipelineError(pipeline)
        click.echo(parsed.model_dump_json(indent=2))
    else:
        click.echo(pipelines_definition.model_dump_json(indent=2))


@main.command()
@click.argument("action", type=click.Choice(["clear", "list"]))
def cache(action: str) -> None:
    cache_dir = utils.get_cache_directory()
    if not os.path.isdir(cache_dir):
        return

    projects = sorted(os.listdir(cache_dir))
    if action == "list":
        click.echo("Caches:")
        click.echo("\n".join(f"\t{p}" for p in projects))
    elif action == "clear":
        for p in projects:
            shutil.rmtree(os.path.join(cache_dir, p))


if __name__ == "__main__":
    main()
