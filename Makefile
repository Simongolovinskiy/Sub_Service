DC = docker compose
.DEFAULT_GOAL := run
.PHONY: run rebuild test

run:
	$(DC) up -d --wait postgres
	$(DC) up --build --no-deps --force-recreate --exit-code-from migrate migrate
	$(DC) up -d --wait app

rebuild:
	$(DC) build --pull --no-cache
	$(MAKE) run

test:
	uv sync --locked
	uv run --locked pytest
