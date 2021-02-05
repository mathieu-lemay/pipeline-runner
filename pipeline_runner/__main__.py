import os
import shutil

import click
import pkg_resources

from . import PipelineRunner
from . import __name__ as project_name
from . import utils
from .config import config


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
def run(pipeline, project_directory, pipeline_file, steps, env_files):
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

    runner = PipelineRunner(pipeline)
    runner.run()


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
