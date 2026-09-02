# Repository Guidelines

## Project Structure & Module Organization

This repository is being established as a Python FastAPI service. Keep application code under `app/`, with the API entry point at `app/main.py`. Group routes, domain logic, schemas, and integrations into focused modules instead of placing all behavior in the entry point. Mirror that organization under `tests/`; for example, test `app/routes/meetings.py` in `tests/routes/test_meetings.py`. Put migration files, scripts, and non-code resources in clearly named top-level directories and document them in `README.md` when introduced.

## Build, Test, and Development Commands

Use `uv` for dependency management and command execution:

- `uv sync` installs the locked development environment.
- `uv run uvicorn app.main:app --reload` starts the local API with reload enabled.
- `uv run pytest` runs the complete test suite.
- `uv run ruff check .` reports lint violations.
- `uv run ruff format --check .` verifies formatting without changing files.

To run the complete local stack, first create the ignored configuration file with
`cp local/.env.example local/.env`. Then run `make up`, or run
`docker compose --env-file local/.env -f local/compose.yaml up --build` directly.

Update `pyproject.toml` and `uv.lock` together whenever dependencies change. Keep the lockfile committed.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations for public functions, and small modules with explicit responsibilities. Name modules, functions, and variables with `snake_case`; classes and Pydantic models with `PascalCase`; constants with `UPPER_SNAKE_CASE`. Let Ruff control formatting and import order. Keep request handlers thin: validation belongs in schemas, while reusable behavior belongs in service modules.

## Testing Guidelines

Use pytest and name files `test_*.py` and test functions `test_<behavior>`. Cover successful requests, invalid input, dependency failures, and authorization boundaries where applicable. Prefer isolated fixtures and dependency overrides over live external services. Every bug fix should include a regression test.

## Commit & Pull Request Guidelines

Use short, imperative subjects such as `Add transcript upload endpoint`, and keep each commit focused. Pull requests should explain the change and motivation, link relevant issues, and list verification commands run. Include request/response examples for API changes and call out configuration, schema, or migration impacts.

## Security & Configuration

Never commit credentials, tokens, recordings, or transcript data. Provide sanitized defaults in `local/.env.example`, document required environment variables, and validate configuration at startup. Avoid logging sensitive request bodies or third-party secrets.
