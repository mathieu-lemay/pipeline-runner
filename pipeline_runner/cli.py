import logging
import os
import shutil
import sys

import click
import pkg_resources

from . import PipelineRunner, PipelinesFileParser
from . import __name__ as project_name
from . import utils
from .config import config

logger = logging.getLogger(__name__)


def _init_logger():
    import coloredlogs

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )

    logger = logging.getLogger(project_name)
    logger.handlers.append(handler)
    logger.setLevel("INFO")

    if config.color:
        coloredlogs.install(level="DEBUG", logger=logger, fmt="%(asctime)s.%(msecs)03d %(name)s: %(message)s")

    docker_logger = logging.getLogger("docker")
    docker_logger.handlers.append(handler)
    docker_logger.setLevel("INFO")


@click.group("Pipeline Runner", invoke_without_command=True)
@click.option(
    "-V",
    "--version",
    "show_version",
    is_flag=True,
    help="Print project version and exit.",
)
@click.pass_context
def main(ctx, show_version):
    if show_version:
        print(f"Pipeline Runner {pkg_resources.get_distribution(project_name).version}")
        ctx.exit()


@main.command()
@click.argument("pipeline")
@click.option(
    "-p",
    "--project-directory",
    help="Root directory of the project. Defaults to current directory.",
)
@click.option(
    "-f",
    "--pipeline-file",
    help="File containing the pipeline definitions. Defaults to 'bitbucket-pipelines.yml'",
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
    help="Enable colored output",
)
def run(pipeline, project_directory, pipeline_file, steps, env_files, color):
    """
    Runs the pipeline PIPELINE.

    PIPELINE is the full path to the pipeline to run. Ex: branches.master
    """
    if project_directory:
        config.project_directory = project_directory

    if pipeline_file:
        config.pipeline_file = pipeline_file

    if steps:
        config.selected_steps = steps

    if env_files:
        config.env_files = env_files

    config.color = color

    _init_logger()

    runner = PipelineRunner(pipeline)
    try:
        runner.run()
    except Exception as e:
        logger.exception(str(e))
        sys.exit(1)


@main.command("list")
@click.option(
    "-p",
    "--project-directory",
    help="Root directory of the project. Defaults to current directory.",
)
@click.option(
    "-f",
    "--pipeline-file",
    help="File containing the pipeline definitions. Defaults to 'bitbucket-pipelines.yml'",
)
@click.option(
    "-c",
    "--color/--no-color",
    default=True,
    help="Enable colored output",
)
def list_(project_directory, pipeline_file, color):
    """
    List the available pipelines.
    """
    if project_directory:
        config.project_directory = project_directory

    if pipeline_file:
        config.pipeline_file = pipeline_file

    config.color = color

    _init_logger()

    pipelines_definition = PipelinesFileParser(config.pipeline_file, expand_vars=False).parse()

    logger.info("Available pipelines:\n\t%s", "\n\t".join(sorted(pipelines_definition.get_available_pipelines())))


@main.command()
@click.argument("action", type=click.Choice(["clear", "list"]))
def cache(action):
    cache_dir = utils.get_user_cache_directory()
    projects = sorted(os.listdir(cache_dir))
    if action == "list":
        print("Caches:")
        print("\n".join(map(lambda i: f"\t{i}", projects)))
    elif action == "clear":
        for p in projects:
            shutil.rmtree(os.path.join(cache_dir, p))


if __name__ == "__main__":
    main()
