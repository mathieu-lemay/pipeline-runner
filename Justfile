app := "pipeline_runner"

run *args: _deps
	DOCKER_CONFIG=./.ci/docker \
		poetry run python -m {{ app }} run {{ args }}

test: _deps
	poetry run pytest -v tests

lint:
	pre-commit run --all

clean:
	rm -f .make.* .coverage

@_deps:
	[[ ! -f .make.poetry || poetry.lock -nt .make.poetry ]] && ( poetry install; touch .make.poetry ) || true

# vim: ft=make
