set dotenv-load := true

app := "pipeline_runner"

lint:
    pre-commit run --all

test: _deps
    poetry run pytest --verbosity=1 --cov --cov-append --cov-report=term-missing:skip-covered --cov-fail-under=90

run *args: _deps
    poetry run python -m {{ app }} {{ args }}

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
    [ ! -f .make.poetry ] || [ poetry.lock -nt .make.poetry ] && ( poetry install --sync; touch .make.poetry ) || true
