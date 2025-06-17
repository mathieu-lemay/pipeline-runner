# Bitbucket Pipeline Runner

Tool to run Bitbucket Pipelines locally.

## Installation
The prefered way of installing pipeline-runner is with [pipx](https://pipx.pypa.io/stable/installation/)
```shell
pipx install bitbucket-pipeline-runner
```

## Basic usage
To run a pipeline
```shell
cd <project-directory>
pipeline-runner run <pipeline-name>
```

To list available pipelines
```shell
cd <project-directory>
pipeline-runner list
```

## Environment variables
bitbucket pipeline runner already sets all `BITBUCKET_*` environment variables in the step's run environment. It will
also source any `.env` file in the current directory, for all project specific environment variables.

## Artifacts and logs
Persistent data like artifacts generated from your pipelines and execution logs can be found in your user's data
directory.

On Linux:

    ${XDG_DATA_HOME:-~/.local/share}/pipeline-runner

On macOS:

    ~/Library/Application Support/pipeline-runner

## Caches
Caches defined in your pipelines are stored in your user's cache directory. Unlike Bitbucket Pipelines, caches are
always saved even if they already exists. This is subject to change in the future, to follow the behaviour of Bitbucket
Pipelines.

On Linux:

    ${XDG_CACHE_HOME:-~/.cache}/pipeline-runner

On macOS:

    ~/Library/Caches/pipeline-runner

Note: Docker cache is stored in a docker volume instead.

## SSH Agent Forwarding
You can expose your ssh-agent to the container running the pipelines. This is useful if the pipeline needs to clone
from a private repository for example.
To do so, run the pipeline with the `--ssh` flag:

```shell
pipeline-runner run --ssh <pipeline-name>
```

> [!NOTE]
Any personal git and/or ssh configuration won't be available inside the
container running the pipeline. If you have more than one ssh key linked to an
account on the target git hosting, ensure that the one you need to use is the
first one in the agent.

## Alternate Platform
To force a different plaform for the pipeline container, you can set the following environment variable:
```shell
# Replace linux/amd64 by the target platform
export PIPELINE_RUNNER_DOCKER_PLATFORM=linux/amd64
```

Note that this affects _only_ the pipeline container, ie. the one that runs your script. It will not
affect the services requested by the pipeline.

If you are running on Apple Silicon, you can force docker to run _all_ containers on a different platform:
```shell
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

> [!WARNING]
This feature is still experimental

## Debugging
A few features are available to help with debugging.

### Breakpoints
Breakpoints, or pauses, can be added during the execution of a pipeline. To do so, add a
`# pipeline-runner[breakpoint]` entry in `script`:
```yaml
    example_with_breakpoint:
      - step:
          name: Step with breakpoint
          script:
            - echo "do something"
            - '# pipeline-runner[breakpoint]'
            - echo "do something else"
```

The execution will stop at the breakpoint to allow the user to check the state of the pipeline.
Note that the entry must be put in quotes to avoid it being interpreted as a yaml comment.

### CPU Limits Enforcing
By default, no cpu limits are enforced, meaning that the pipeline will run as fast as it can. You can mimick the cpu
limits enforced by Bitbucket Pipelines with the `--cpu-limits`. This is useful to replicate more closely the speed at
which a pipeline runs in the real thing.

## Supported features
Most features of Bitbucket Pipelines should work out of the box. If you find something that is not working properly,
please open an issue.
| Feature               | Supported  | Note                                                       |
| --------------------- | :--------: | :--------------------------------------------------------: |
| Variables             | ✅         |                                                            |
| Artifacts             | ✅         |                                                            |
| Docker Service        | ✅         |                                                            |
| Caches                | ✅         |                                                            |
| Custom Caches         | ✅         |                                                            |
| Private Runner Images | ✅         |                                                            |
| Pipes                 | ✅         |                                                            |
| Parallel Steps        | ✅         | The steps will run, but in sequence.                       |
| OIDC                  | ✅         | [OIDC Setup](https://github.com/mathieu-lemay/pipeline-runner/wiki/OIDC) |
