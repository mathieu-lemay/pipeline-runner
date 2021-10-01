set dotenv-load := true

app := "pipeline_runner"

run *args: _deps
    poetry run python -m {{ app }} {{ args }}

run-pipeline *args: _deps
    DOCKER_CONFIG=./.ci/docker \
        poetry run python -m {{ app }} run {{ args }}

test: _deps
    poetry run pytest -v tests -m "not integration"

integration-tests: _deps
    poetry run pytest -v tests

coverage: _deps
    poetry run coverage run -m pytest -v tests
    poetry run coverage report

lint:
    pre-commit run --all

release version:
    #!/bin/sh
    set -eu
    poetry version "{{ version }}"
    version="$(poetry version -s)"
    git add pyproject.toml
    git commit -m "Bump version to ${version}"
    git tag -s -a -m "Version ${version}" "${version}"

clean:
    rm -f .make.* .coverage

@_deps:
    [[ ! -f .make.poetry || poetry.lock -nt .make.poetry ]] && ( poetry install; touch .make.poetry ) || true
