.PHONY: build up db-up down migrate migration ingest test test-unit test-integration integration-db-up integration-db-down lint format

LOCAL_ENV ?= local/.env
COMPOSE = docker compose --env-file $(LOCAL_ENV) -f local/compose.yaml
INTEGRATION_DATABASE_URL = postgresql+psycopg://postgres:postgres@localhost:55432/meeting_transcripts_test
INTEGRATION_COMPOSE = POSTGRES_PORT=55432 POSTGRES_DB=meeting_transcripts_test POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres docker compose -p meeting-transcript-api-tests --env-file $(LOCAL_ENV) -f local/compose.yaml

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --wait db
	$(MAKE) migrate
	$(COMPOSE) up --build

db-up:
	$(COMPOSE) up -d db

down:
	$(COMPOSE) down

migrate:
	uv run alembic upgrade head

migration:
	test -n "$(MESSAGE)" || (echo "Usage: make migration MESSAGE='describe change'" && exit 1)
	uv run alembic revision --autogenerate -m "$(MESSAGE)"

ingest:
	$(COMPOSE) run --rm api uv run --no-sync ingest-mised --dataset-dir /data/mised

test: test-unit test-integration

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

integration-db-up:
	$(INTEGRATION_COMPOSE) up -d --wait db
	DATABASE_URL=$(INTEGRATION_DATABASE_URL) uv run alembic upgrade head

integration-db-down:
	$(INTEGRATION_COMPOSE) down --volumes --remove-orphans

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .
