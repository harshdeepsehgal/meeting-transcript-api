# Meeting Transcript API

A FastAPI service that ingests the [MISeD dataset](https://github.com/google-research-datasets/MISeD), exposes read APIs for meeting dialogs, and summarizes full meeting transcripts with OpenAI.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose

An OpenAI API key is optional for startup, ingestion, and the dialog read APIs. It is required for an uncached summary or a summary refresh.

## Local setup

Copy the sanitized local configuration and install the locked environment:

```bash
cp local/.env.example local/.env
uv sync --locked
```

Start PostgreSQL and apply the Alembic schema:

```bash
make db-up
make migrate
```

The bootstrap SQL creates the `meeting_transcript` schema. The application tables are created by Alembic. Application queries use async SQLAlchemy with Psycopg 3; Alembic uses the same database URL with its synchronous driver.

## Dataset ingestion

Place the locally supplied, uncommitted MISeD files at:

```text
mised/train.jsonl
mised/validation.jsonl
mised/test.jsonl
```

The application does not download the dataset. Import all three files with:

```bash
uv run ingest-mised --dataset-dir mised
# or, from the Docker Compose stack:
make ingest
```

The command prints one JSON report with `created`, `updated`, `skipped`, and safe `errors` fields. Its exit codes are:

- `0`: every source record was imported successfully;
- `1`: valid records were committed but one or more malformed records were skipped;
- `2`: a required file, configuration, or database failure prevented the import.

Re-importing a source dialog updates its rows, replaces its transcript and turns, and does not modify cached meeting summaries.

## Run the API

For host-side development, start the API after PostgreSQL and migrations are ready:

```bash
uv run uvicorn app.main:app --reload
```

The service listens on `http://localhost:8000`. Swagger UI is at `http://localhost:8000/docs`; the generated schema is at `http://localhost:8000/openapi.json`.

To run the complete local stack with Docker Compose:

```bash
make up
```

The complete stack applies migrations before starting the API. Run `make ingest` to
import the files mounted from the local `mised` directory. Compose exposes the API
on port `8000` and PostgreSQL on port `5432` by default; override them with
`API_PORT` and `POSTGRES_PORT` in `local/.env`.

## Configuration

Runtime settings are read from environment variables and `local/.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Runtime environment label |
| `LOG_LEVEL` | `INFO` | Application log level |
| `DATABASE_URL` | Local PostgreSQL URL | Host-side SQLAlchemy database URL |
| `POSTGRES_DB` | `meeting_transcripts` | Compose database name |
| `POSTGRES_USER` | `postgres` | Compose database user |
| `POSTGRES_PASSWORD` | `postgres` | Compose database password for local development |
| `POSTGRES_PORT` | `5432` | Published Compose PostgreSQL port |
| `API_PORT` | `8000` | Published Compose API port |
| `OPENAI_API_KEY` | empty | Required only when generating or refreshing a missing summary |
| `OPENAI_MODEL` | `gpt-5.6-terra` | OpenAI Responses API model |
| `OPENAI_TIMEOUT_SECONDS` | `60` | OpenAI request timeout |
| `OPENAI_MAX_RETRIES` | `2` | OpenAI client retry count |

Never commit `local/.env`, API keys, recordings, transcripts, or the MISeD dataset.

## API examples

### List dialogs

```bash
curl 'http://localhost:8000/dialogs?limit=20'
```

```json
{
  "items": [
    {"dialog_id": "dialog-id", "meeting_id": "meeting-id"}
  ],
  "next_cursor": "dialog-id-or-null"
}
```

Use the returned `next_cursor` as the `cursor` query parameter for the next page:

```bash
curl 'http://localhost:8000/dialogs?limit=20&cursor=dialog-id'
```

`limit` must be between 1 and 100. An empty cursor or invalid limit returns `422`.

### Retrieve a dialog

```bash
curl 'http://localhost:8000/dialogs/dialog-id'
```

```json
{
  "dialog_id": "dialog-id",
  "meeting_id": "meeting-id",
  "transcript": [
    {"position": 0, "speaker": "Speaker A", "text": "Transcript text"}
  ],
  "turns": [
    {
      "position": 0,
      "query": "Question",
      "query_metadata": {},
      "response": "Answer",
      "attributions": [],
      "references": []
    }
  ]
}
```

Transcript segments and turns are returned in ascending source `position`. An unknown dialog returns `404` with `{"detail":"Dialog not found"}`.

### Summarize a meeting transcript

Return the cached summary when one exists:

```bash
curl -X POST 'http://localhost:8000/dialogs/dialog-id/summary'
```

```json
{"summary": "Plain-text meeting summary"}
```

Regenerate and replace the cached summary with:

```bash
curl -X POST 'http://localhost:8000/dialogs/dialog-id/summary?refresh=true'
```

The cache is keyed only by `meeting_id`, so dialogs for the same meeting share one summary. A missing API key returns `503`; a transcript that exceeds the model context returns `422`; other provider failures return `502`. The service never truncates, chunks, or partially summarizes a transcript.

### Generate dialog responses

Generate answers for every stored query in a dialog with one OpenAI request:

```bash
curl -X POST 'http://localhost:8000/dialogs/dialog-id/responses'
```

```json
[
  {
    "query": "What was decided?",
    "storedResponse": "The stored MISeD answer.",
    "generatedResponse": "The generated answer.",
    "error": null
  }
]
```

The request sends the complete transcript and all ordered queries, requires strict JSON output,
and atomically stores the validated generated answers. Every POST regenerates the batch. For a
known dialog, configuration, context-limit, output-validation, and provider failures return one
error item per stored query with `generatedResponse` set to `null`; previous generated answers are
left unchanged.

## Postman

Import [`postman/meeting-transcript-api.postman_collection.json`](postman/meeting-transcript-api.postman_collection.json) into Postman. Set the `base_url` and `dialog_id` collection variables for the local environment. The collection includes list, detail, summary, and dialog-response requests.

## Tests

```bash
make test              # Run unit and integration tests
make test-unit         # Run unit tests only
make test-integration  # Run integration tests only
```

Unit tests do not require external services. Integration tests require Docker and
automatically start, migrate, and remove a dedicated PostgreSQL database on port
`55432`, including its test data and volume.

## Other quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run alembic check
docker compose --env-file local/.env -f local/compose.yaml config
git diff --check
```

See [`SPEC.md`](SPEC.md) for the complete data model and API contract and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for implementation stages.
