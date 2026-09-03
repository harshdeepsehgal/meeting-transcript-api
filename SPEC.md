# Project Requirements

## 1. Dataset

- Use the MISeD dataset as the source dataset.
- Developers will supply the dataset locally; downloading the dataset is not part of the application.
- Ingest data from the JSONL files under the `mised` directory. The dataset files must remain uncommitted.
- One imported record represents one complete dialog, not an individual query-response task instance.
- Import all supplied `train`, `validation`, and `test` files in one run.
- Ingest the complete dialog data, including:
  - Dialog and meeting identifiers
  - Meeting transcript
  - LLM/user queries and their query metadata
  - LLM responses
  - Any attribution or reference metadata present in the dataset

## 2. Backend Database

- Use PostgreSQL as the backend database for the ingested MISeD data.
- The data model must support efficient access to dialogs and their associated transcript and query/response data.
- The ingestion process must be reproducible and idempotent. Re-importing the same source dialog must update it rather than create a duplicate.
- Malformed records must be skipped without preventing valid records from loading. Ingestion must report created, updated, and skipped counts, describe invalid records, and exit non-zero when any records are skipped.
- Database schema changes must be applied through migrations.

## 3. Data APIs

- Build APIs to query the data stored in the database.
- Provide `GET /dialogs` to retrieve a compact list of dialogs. It must use bounded cursor pagination.
- Provide `GET /dialogs/{dialog_id}` to retrieve a complete dialog, including its dialog and meeting identifiers, full meeting transcript, query-response turns, query metadata, and attribution or reference data.
- No additional search, transcript, query, or response endpoints are required.
- APIs must return structured JSON responses.
- A request for an unknown dialog must return `404 Not Found`.

## 4. Summarization API

- Provide `POST /dialogs/{dialog_id}/summary` to generate a summary of the full meeting transcript associated with a dialog.
- The summarization API must use the OpenAI API.
- The summary must describe the meeting itself, not the dataset's query-response dialog.
- The API must return structured JSON whose generated content is a single plain-text summary field.
- Cache one summary per meeting transcript, keyed only by `meetingId`. Dialogs associated with the same meeting must share the cached summary. Return the cached summary by default and support `refresh=true` to request regeneration. Ingestion and re-ingestion must not modify cached summaries.
- If the full transcript exceeds the configured model's context limit, return `422 Unprocessable Entity`; do not truncate or chunk the transcript.
- No transcript question-answering API is required.
- No partial or chunk-level summarization API is required.

## 5. Configuration

- The OpenAI API key must be provided through configuration or environment variables.
- The API must be able to start without an OpenAI API key, but summarization must fail clearly until a key is configured.
- Any other runtime configuration required by the application must be documented.
- Authentication, authorization, and rate limiting are not required for this prototype.

## 6. Local Development

- The project must be runnable locally.
- Local Docker Compose is the only deployment target required for this prototype.
- Include setup instructions for:
  - Installing dependencies
  - Configuring required environment variables
  - Starting the database
  - Running the ingestion process
  - Starting the API server
- Include any required database initialization or migration steps.

## 7. API Documentation

- Provide FastAPI-generated OpenAPI and Swagger documentation covering all implemented endpoints.
- Document request parameters, request bodies where applicable, and response structures.
- Document expected error responses.

## 8. Postman Collection

- Include a Postman collection covering the implemented APIs.
- The collection must include requests for:
  - Listing dialogs
  - Retrieving a dialog by ID
  - Summarizing a dialog's full meeting transcript

## 9. Out of Scope

- Transcript search and query-metadata filtering.
- Separate transcript, query, response, or attribution endpoints.
- Transcript question answering.
- Partial, chunked, or truncated summarization.
- User accounts, authentication, authorization, and rate limiting.
- Cloud infrastructure or production deployment automation.

## 10. Repository Deliverable

- Publish the completed project to a GitHub repository.
- The repository must include:
  - Application source code
  - Database schema or model definitions
  - Dataset ingestion code
  - API implementation
  - OpenAI summarization integration
  - API documentation/specification
  - Postman collection
  - Local setup and run instructions
