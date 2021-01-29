import click


@click.command("Pipeline Runner")
@click.option("-e", "--env-file", help="Read in a file of environment variables")
def main(env_file):
    print(f"env_file: {env_file}")
