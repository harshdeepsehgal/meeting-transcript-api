-- Runs once when the local PostgreSQL data volume is first initialized.
CREATE SCHEMA IF NOT EXISTS meeting_transcript;
COMMENT ON SCHEMA meeting_transcript IS 'Application schema for meeting transcript data.';
