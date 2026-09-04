# Meeting Transcript API Implementation Plan

## 1. Goal

Implement `SPEC.md` on top of the existing FastAPI, async SQLAlchemy, Alembic, OpenAI, `uv`, and
Docker Compose scaffold.

The application will:

- ingest all local MISeD `train`, `validation`, and `test` JSONL files in one command;
- store shared meeting transcripts, complete dialogs, turns, attributions, and references in
  PostgreSQL;
- expose the four required dialog APIs;
- summarize a dialog's complete meeting transcript through OpenAI;
- cache one summary per meeting transcript; and
- include migrations, tests, OpenAPI documentation, a Postman collection, and local setup
  instructions.

Do not implement dataset downloading, transcript search, chunking, ad hoc question answering,
authentication, rate limiting, or cloud deployment.

## 2. Simple Application Design

### 2.1 Modules

Keep the existing foundation and add only these focused modules:

```text
app/
  main.py                    # FastAPI app and router registration
  core/config.py             # existing environment settings
  db/base.py                 # existing SQLAlchemy base
  db/session.py              # existing engine and session dependency
  db/models.py               # all five ORM models
  schemas/dialogs.py         # public API response models
  services/dialogs.py        # list and detail SQLAlchemy queries
  services/ingestion.py      # JSONL validation, normalization, and writes
  services/summarization.py  # cache lookup and summary orchestration
  routes/dialogs.py          # the four required endpoints
  integrations/openai.py     # existing provider plus summary request helper
  cli/ingest.py              # existing CLI entry point
```

Do not add repository interfaces, a separate pagination component, custom exception classes,
provider protocols, or separate internal DTO packages. Services receive an `AsyncSession` and run
SQLAlchemy statements directly. Routes validate HTTP input, call a service, and translate expected
failures to `HTTPException`.

### 2.2 Database Schema

Use the existing `meeting_transcript` PostgreSQL schema. Create the following tables through one
Alembic revision.

#### `transcripts`

One row represents one meeting transcript shared by all dialogs with the same MISeD `meetingId`.

| Column | Type | Rules |
| --- | --- | --- |
| `meeting_id` | TEXT | Primary key; mapped from `meetingId` |

#### `transcript_segments`

One row represents one transcript segment in source order.

| Column | Type | Rules |
| --- | --- | --- |
| `meeting_id` | TEXT | FK to `transcripts.meeting_id`, `ON DELETE CASCADE`; primary-key part |
| `position` | INTEGER | Zero-based, non-negative; primary-key part |
| `speaker` | TEXT | Nullable |
| `text` | TEXT | Non-null and non-empty |

The composite primary key `(meeting_id, position)` provides ordered transcript access. Do not add
timestamps, source-segment IDs, search indexes, embeddings, or chunk tables.

#### `dialogs`

| Column | Type | Rules |
| --- | --- | --- |
| `dialog_id` | TEXT | Primary key; stable MISeD dialog identifier and public API ID |
| `meeting_id` | TEXT | Non-null FK to `transcripts.meeting_id`, `ON DELETE RESTRICT` |

The primary-key index on `dialog_id` supports cursor pagination; no additional dialog index is
required.

#### `dialog_turns`

| Column | Type | Rules |
| --- | --- | --- |
| `dialog_id` | TEXT | FK to `dialogs.dialog_id`, `ON DELETE CASCADE`; primary-key part |
| `position` | INTEGER | Zero-based, non-negative; primary-key part |
| `query` | TEXT | Non-null and non-empty |
| `query_metadata` | JSONB | Non-null; query metadata object supplied by MISeD |
| `response` | TEXT | Non-null |
| `attributions` | JSONB | Non-null, default `[]` |
| `references` | JSONB | Non-null, default `[]` |

Use `(dialog_id, position)` as the composite primary key.

#### `transcript_summaries`

| Column | Type | Rules |
| --- | --- | --- |
| `meeting_id` | TEXT | Primary key and FK to `transcripts.meeting_id`, `ON DELETE CASCADE` |
| `summary` | TEXT | Non-null and non-empty plain text |

Store exactly one cached summary per `meeting_id`. The configured model is used for generation but
is not stored and is not part of cache identity.

Keep only metadata containers that exist in MISeD: turn `query_metadata`, `attributions`, and
`references`. Do not add metadata columns to transcripts, transcript segments, or dialogs, and do
not invent generic containers for unknown fields.

### 2.3 Transcript Rendering

Render the full transcript by loading segments in ascending `position` order and joining them with
newlines. Format a segment as `speaker: text` when `speaker` exists, otherwise use `text`.

No search, chunk, or partial-summary behavior is part of this implementation.

## 3. Required Behavior

### 3.1 Ingestion

The existing command remains:

```bash
uv run ingest-mised --dataset-dir mised
```

The directory must contain:

```text
train.jsonl
validation.jsonl
test.jsonl
```

Before implementing the parser, inspect one locally supplied record and confirm the exact keys for
the dialog ID, `meetingId`, transcript segments, queries, query metadata, responses, attributions,
and references. Encode that shape in small synthetic test fixtures; do not commit real MISeD
content.

Processing rules:

1. Verify that all three files exist before importing anything.
2. Read the files sequentially in train, validation, test order.
3. Read one line at a time and decode a JSON object.
4. Validate the required dialog ID, `meetingId`, non-empty transcript segments, and ordered turns.
5. Map only the confirmed MISeD fields. Preserve query metadata, attributions, and references as
   their named JSONB columns; do not add generic storage for unknown fields.
6. For each valid record, open a database transaction:
   - upsert `transcripts` by `meeting_id`;
   - replace that transcript's segments;
   - upsert `dialogs` by `dialog_id`;
   - replace that dialog's turns; and
   - commit.
7. If JSON decoding or record validation fails, roll back that record, add an error entry, and
   continue with the next line.
8. Treat missing files, database failures, and other command-level failures as fatal.

Never load an entire JSONL file into memory. At most one complete dialog record and its normalized
segments/turns should be held during ingestion, so memory usage is bounded by the largest individual
record rather than the dataset size.

Replacing child rows makes re-imports deterministic: removed source segments or turns cannot remain
in the database. Existing `dialog_id` values count as updated; new IDs count as created.
Ingestion and re-ingestion must not read, update, or delete `transcript_summaries` rows.

Print one JSON report to stdout:

```json
{
  "created": 10,
  "updated": 2,
  "skipped": 1,
  "errors": [
    {
      "file": "train.jsonl",
      "line": 8,
      "dialog_id": "known-id-or-null",
      "message": "safe validation message"
    }
  ]
}
```

Exit with:

- `0` when no records were skipped;
- `1` when valid records were committed but one or more malformed records were skipped; or
- `2` for a fatal file, configuration, or database failure.

Do not print or log complete records, transcript text, query/response bodies, secrets, or database
credentials.

### 3.2 `GET /dialogs`

Query parameters:

- `limit`: default `20`, minimum `1`, maximum `100`;
- `cursor`: optional non-empty last returned `dialog_id`.

Query `dialogs` in `dialog_id ASC` order. When a cursor exists, add `dialog_id > cursor`. Fetch
`limit + 1` rows, return at most `limit`, and set `next_cursor` to the last returned ID only when
another row exists.

Response:

```json
{
  "items": [
    {
      "dialog_id": "dialog-id",
      "meeting_id": "meeting-id"
    }
  ],
  "next_cursor": "dialog-id-or-null"
}
```

### 3.3 `GET /dialogs/{dialog_id}`

Load the dialog, its transcript segments ordered by position, and its turns ordered by position.
Return per-turn query metadata in its named field.

Response:

```json
{
  "dialog_id": "dialog-id",
  "meeting_id": "meeting-id",
  "transcript": [
    {
      "position": 0,
      "speaker": "Speaker A",
      "text": "Transcript text"
    }
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

Return `404` with `{"detail": "Dialog not found"}` for an unknown ID.

### 3.4 `POST /dialogs/{dialog_id}/summary`

Accept the optional boolean query parameter `refresh`, defaulting to `false`. There is no request
body. Return exactly:

```json
{"summary": "Plain-text meeting summary"}
```

Processing order:

1. Load the dialog and its complete transcript; return `404` if it does not exist.
2. If `refresh=false`, return the cache row for `meeting_id`. All dialogs for the same meeting use
   this row. Ingestion and re-ingestion do not affect it.
3. Build the OpenAI provider lazily. If no key is configured, return `503`; API startup and read
   endpoints must continue to work without a key.
4. Send one request containing the complete rendered meeting transcript. Instruct the model to
   summarize the meeting, not the dataset query/response turns.
5. Disable provider-side truncation. Do not truncate, split, or chunk the transcript.
6. Translate the provider's context-limit rejection to `422`.
7. Translate other provider failures, including timeouts or empty output, to `502` without exposing
   provider details.
8. After receiving non-empty plain text, upsert the summary by `meeting_id` and return it. Changing
   the configured model does not invalidate an existing cache entry; use `refresh=true` to replace
   it.

A failed generation or refresh must not overwrite a previously valid cache row.

### 3.5 `POST /dialogs/{dialog_id}/responses`

Send the complete transcript and every ordered query in one OpenAI Responses API call. Require
strict JSON containing each query position, unchanged query, generated response, and ordered
transcript-position attribution ranges. Validate the complete batch before atomically storing
response text on the dialog turns; generated attributions remain request-scoped. Every POST regenerates;
provider or output-validation failures return `200` with safe per-item errors and preserve prior
generated responses. Re-ingestion replaces dialog turns and deletes their generated responses.

### 3.6 OpenAPI Errors

Use FastAPI response models for successful responses and `HTTPException` for application errors.
Document the applicable error codes on each route:

| Condition | Result |
| --- | --- |
| Unknown dialog | `404` |
| Invalid limit, cursor, or refresh value | `422` |
| Transcript exceeds provider context | `422` |
| OpenAI key missing for generation | `503` |
| Other OpenAI failure | `502` |
| Response batch configuration or provider failure | `200` with per-item errors |

FastAPI's `/docs` and `/openapi.json` must describe all four endpoints, parameters, response
schemas, and expected errors.

## 4. Implementation Tasks

Complete these stages in order.

### T1 - Confirm Mapping and Add the Database Schema

- [ ] Inspect the local MISeD shape and create invented train/validation/test fixtures matching its
  turn query metadata.
- [ ] Add invalid fixture lines for malformed JSON, missing IDs, empty transcripts, and invalid
  turns.
- [ ] Implement the five SQLAlchemy models in `app/db/models.py`.
- [ ] Import the models into Alembic metadata discovery.
- [ ] Generate and review one migration containing only the five tables, constraints, and foreign
  keys.
- [ ] Test a clean migration upgrade and basic constraints against PostgreSQL.

Completion check:

```bash
make db-up
uv run alembic upgrade head
uv run alembic check
uv run pytest tests/test_database.py
```

### T2 - Implement Ingestion

- [ ] Replace the CLI placeholder with an async ingestion entry point.
- [ ] Validate all required files before processing.
- [ ] Implement JSON decoding and source-to-database normalization in the ingestion service.
- [ ] Implement the shared transcript renderer.
- [ ] Implement per-record transactions, transcript/dialog upserts, and child-row replacement
  without reading or writing summary rows.
- [ ] Produce the required report and exit codes.
- [ ] Replace the placeholder CLI test with ingestion tests covering:
  - successful import across all files;
  - two dialogs sharing one transcript;
  - identical re-import without duplicate rows;
  - re-import leaving an existing meeting summary unchanged;
  - malformed records skipped while valid records commit; and
  - missing file or database failure returning exit `2`.

Completion check:

```bash
uv run ingest-mised --dataset-dir tests/fixtures/mised
uv run pytest tests/test_ingest_cli.py tests/services/test_ingestion.py
```

### T3 - Implement Dialog Read APIs

- [ ] Add public list, detail, transcript segment, and turn response models.
- [ ] Implement list and detail SQLAlchemy queries in `app/services/dialogs.py`.
- [ ] Implement bounded cursor pagination by `dialog_id`.
- [ ] Add the two GET routes and register the router in `create_app`.
- [ ] Replace the empty-OpenAPI scaffold test.
- [ ] Test empty and multi-page lists, limit bounds, ordered detail content, and unknown-dialog
  `404`.

Completion check:

```bash
uv run pytest tests/test_app.py tests/routes/test_dialogs.py
```

### T4 - Implement Summarization and Caching

- [ ] Extend the existing OpenAI integration with one full-transcript summary request.
- [ ] Keep provider construction lazy and truncation disabled.
- [ ] Implement `meeting_id` cache lookup, `refresh`, and PostgreSQL cache upsert in the
  summarization service.
- [ ] Add the POST route and document its success and error responses.
- [ ] Test cache hit, cache miss, two dialogs sharing one meeting cache, configured-model changes
  retaining the cache, refresh, missing key, provider context rejection, generic provider failure,
  and unknown dialog using a mocked OpenAI client.
- [ ] Do not require a live OpenAI request in automated tests.

Completion check:

```bash
uv run pytest tests/integrations/test_openai.py tests/routes/test_summaries.py
```

### T5 - Complete Documentation and Postman

- [ ] Update `README.md` with dependency installation, `.env` setup, database startup, migration,
  ingestion, API startup, Docker Compose, testing, and endpoint examples.
- [ ] Document the existing OpenAI settings and that the key is optional at startup.
- [ ] Document ingestion exit codes and required local dataset paths.
- [ ] Add a Postman v2.1 collection with variables for `base_url`, `dialog_id`, and `cursor`.
- [ ] Include list, detail, cached-summary, and refreshed-summary requests.
- [ ] Validate the collection JSON in a test and confirm it contains all four required endpoints.
- [ ] Confirm `.gitignore` continues to exclude MISeD files and credentials.

### T6 - Final Verification

- [ ] Start PostgreSQL and migrate a clean database.
- [ ] Ingest synthetic fixtures twice and confirm stable row counts.
- [ ] Verify a mixed valid/invalid import commits valid rows and exits `1`.
- [ ] Start the API without an OpenAI key and verify both read endpoints work.
- [ ] Verify an uncached summary returns `503` without a key.
- [ ] Verify `/docs`, `/openapi.json`, and the Postman collection match the implemented contracts.
- [ ] Publish the verified application source, migration, documentation, tests, and Postman
  collection to the target GitHub repository.
- [ ] Run the complete check suite:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
docker compose --env-file local/.env -f local/compose.yaml config
uv run alembic check
git diff --check
```

## 5. Definition of Done

- All five normalized tables exist through Alembic and use MISeD natural identifiers.
- All three files import in one command, and re-importing does not create duplicates.
- Malformed records are reported and skipped without blocking valid records.
- `GET /dialogs` is bounded and cursor-paginated.
- `GET /dialogs/{dialog_id}` returns the complete transcript, dialog turns, query metadata,
  attributions, and references.
- `POST /dialogs/{dialog_id}/summary` summarizes the full meeting transcript, shares one cache row
  per `meeting_id`, supports refresh, and never truncates or chunks input.
- Ingestion and re-ingestion never modify existing summary cache rows.
- The API starts without an OpenAI key and clearly rejects uncached generation until configured.
- Swagger/OpenAPI, Postman, README, migrations, tests, and local Docker Compose instructions agree.
- No credentials, recordings, transcripts, or MISeD dataset files are committed or logged.
