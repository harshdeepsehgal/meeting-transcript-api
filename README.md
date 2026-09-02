# Meeting Transcript API

A FastAPI service that will ingest the [MISeD dataset](https://github.com/google-research-datasets/MISeD), expose read APIs for meeting dialogs, and summarize full transcripts with OpenAI.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose
- An OpenAI API key

## Local Python Setup

```bash
cp local/.env.example local/.env
uv sync
uv run uvicorn app.main:app --reload
```

The service is available at `http://localhost:8000`; Swagger UI is at `/docs`.

Start PostgreSQL separately and apply migrations with:

```bash
docker compose --env-file local/.env -f local/compose.yaml up -d db
uv run alembic upgrade head
```

The bootstrap SQL creates the `meeting_transcript` schema only. Future application tables and
extensions must be introduced through Alembic revisions.

Application database access uses async SQLAlchemy sessions through Psycopg 3. Alembic uses the
same driver synchronously for one-shot migrations.

## Docker Compose

Copy `local/.env.example` to `local/.env`, then run the complete local stack:

```bash
docker compose --env-file local/.env -f local/compose.yaml up --build
```

Compose exposes the API on port `8000` and PostgreSQL on `5432` by default. Override them with
`API_PORT` and `POSTGRES_PORT`. Database data persists in the `postgres_data` volume. Bootstrap
SQL runs only when that volume is first initialized.

## Dataset and Ingestion

Place the uncommitted dataset files at:

```text
mised/train.jsonl
mised/validation.jsonl
mised/test.jsonl
```

The ingestion command is:

```bash
uv run ingest-mised --dataset-dir mised
# or: make ingest
```

## Configuration

Runtime settings are environment-driven; see `local/.env.example`. `OPENAI_API_KEY` is optional during
startup and required only when constructing the OpenAI provider. `OPENAI_MODEL` selects the model
for future Responses API calls. Do not commit `local/.env`, API keys, or MISeD data.

## Quality Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker compose --env-file local/.env -f local/compose.yaml config
```

See `SPEC.md` for the data model, ingestion, read endpoints, summarization endpoint, API
documentation, and Postman collection requirements.
