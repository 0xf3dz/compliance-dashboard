-- Ironbark Ridge ESG schema: AI findings.
--
-- This file only creates. No script drops incident_ai_findings, so a re-ingest
-- of the CSVs leaves the findings intact. The findings cost a paid API call
-- each and are not derivable from the source files.
--
-- Two deliberate departures from a conventional design:
--
-- 1. There is no foreign key to incidents(id). schema.sql drops and recreates
--    incidents on every run, so the surrogate id is renumbered and a finding
--    keyed on it would point at the wrong row, or at nothing.
--
-- 2. The join key is source_row, the CSV data-row number that the pipeline
--    assigns as index + 1. It is stable across re-ingests because it is a
--    property of the file, not of the load.
--
-- description_sha256 is the SHA-256 hex digest of the incident description at
-- classification time. classify.py compares it to skip unchanged work, and
-- the API compares it to mark a finding stale when the source text changed.

CREATE TABLE IF NOT EXISTS incident_ai_findings (
    id                 serial PRIMARY KEY,
    incident_id        text NOT NULL,
    source_row         int  NOT NULL,
    description_sha256 text NOT NULL,
    ai_category        text,
    is_psychosocial    bool,
    severity_mismatch  bool,
    mismatch_detail    text,
    evidence_quote     text,
    rationale          text,
    model              text,
    prompt_version     text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- One finding per incident. classify.py upserts through this index.
CREATE UNIQUE INDEX IF NOT EXISTS ux_incident_ai_findings_source_row
    ON incident_ai_findings (source_row);
