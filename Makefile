.PHONY: build up db-up down migrate migration ingest test lint format

LOCAL_ENV ?= local/.env
COMPOSE = docker compose --env-file $(LOCAL_ENV) -f local/compose.yaml

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build

db-up:
	$(COMPOSE) up -d db

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) run --rm api uv run --no-sync alembic upgrade head

migration:
	test -n "$(MESSAGE)" || (echo "Usage: make migration MESSAGE='describe change'" && exit 1)
	$(COMPOSE) run --rm api uv run --no-sync alembic revision --autogenerate -m "$(MESSAGE)"

ingest:
	$(COMPOSE) run --rm api uv run --no-sync ingest-mised --dataset-dir /data/mised

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .
