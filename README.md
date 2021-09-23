# Bitbucket Pipeline Runner

Tool to run Bitbucket Pipelines locally.

## Installation
```shell
pip install bitbucket-pipeline-runner
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
bitbucket pipeline runner already sets all `BITBUCKET_*` enviromnent variables in the step's run enviromnent.
It will also source any `.env` file in the current directory, for all project specific enviromnent variables.

## Artifacts and logs
Persistent data like artifacts generated from your pipelines and execution logs can be found in your user's data directory.

On Linux:

    ${XDG_DATA_HOME:-~/.local/share}/pipeline-runner

On macOS:

    ~/Library/Application Support/pipeline-runner

## Caches
Caches defined in your pipelines are stored in your user's cache directory.

On Linux:

    ${XDG_CACHE_HOME:-~/.cache}/pipeline-runner

On macOS:

    ~/Library/Caches/pipeline-runner
