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
bitbucket pipeline runner already sets all `BITBUCKET_*` environment variables in the step's run environment.
It will also source any `.env` file in the current directory, for all project specific environment variables.

## Artifacts and logs
Persistent data like artifacts generated from your pipelines and execution logs can be found in your user's data directory.

On Linux:

    ${XDG_DATA_HOME:-~/.local/share}/pipeline-runner

On macOS:

    ~/Library/Application Support/pipeline-runner

## Caches
Caches defined in your pipelines are stored in your user's cache directory. Unlike Bitbucket Pipelines, caches are always
saved even if they already exists. This is subject to change in the future, to follow the behaviour of Bitbucket Pipelines.

On Linux:

    ${XDG_CACHE_HOME:-~/.cache}/pipeline-runner

On macOS:

    ~/Library/Caches/pipeline-runner

Note: Docker cache is stored in a docker volume instead.

## Supported features
| Feature               | Supported  | Note                                           |
| --------------------- | :--------: | :--------------------------------------------: |
| Variables             | ✅         |                                                |
| Artifacts             | ✅         |                                                |
| Docker Service        | ✅         |                                                |
| Caches                | ✅         |                                                |
| Custom Caches         | ✅         |                                                |
| Private Runner Images | ✅         |                                                |
| Pipes                 | ✅         |                                                |
| OIDC                  | ❌         | Theoretically possible but way too impractical |

## Debugging
A few features are available to help with debugging.

### Breakpoints
Breakpoints, or pauses, can be added during the execution of a pipeline. To do so, add a `# pipeline-runner[breakpoint]` entry in `script` like so
```
    example_with_breakpoint:
      - step:
          name: Step with breakpoint
          script:
            - echo "do something"
            - # pipeline-runner[breakpoint]
            - echo "do something else"
```

The execution will stop at the breakpoint to allow the user to check the state of the pipeline.

### CPU Limits Enforcing
By default, no cpu limits are enforced, meaning that the pipeline will run as fast as it can.
You can mimick the cpu limits enforced by Bitbucket Pipelines with the `--cpu-limits`. This is
useful to replicate more closely the speed at which a pipeline runs in the real thing.
