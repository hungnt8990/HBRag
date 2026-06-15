-- Optional PostgreSQL indexes for enriched embedding/search metadata.
-- No schema migration is required because chunks.metadata is already JSONB.
-- Run this once after deploying the embedding metadata patch.

-- Exact lookup / containment over JSON arrays such as:
-- metadata->'identifiers' = ["3113", "3113/EVN-KDMBĐ", "EVN-KDMBĐ"]
CREATE INDEX IF NOT EXISTS ix_chunks_metadata_identifiers_gin
ON chunks USING GIN ((metadata -> 'identifiers'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_doc_codes_gin
ON chunks USING GIN ((metadata -> 'doc_codes'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_dates_gin
ON chunks USING GIN ((metadata -> 'dates'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_screen_names_gin
ON chunks USING GIN ((metadata -> 'screen_names'));

-- Common scalar filters stored inside metadata.
CREATE INDEX IF NOT EXISTS ix_chunks_metadata_platform
ON chunks ((metadata ->> 'platform'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_phase
ON chunks ((metadata ->> 'phase'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_change_type
ON chunks ((metadata ->> 'change_type'));

CREATE INDEX IF NOT EXISTS ix_chunks_metadata_content_type
ON chunks ((metadata ->> 'content_type'));

-- Helpful exact-match examples:
-- SELECT id, document_id, chunk_index
-- FROM chunks
-- WHERE metadata -> 'identifiers' ? '3113';
--
-- SELECT id, document_id, chunk_index
-- FROM chunks
-- WHERE metadata -> 'doc_codes' ? '3113/EVN-KDMBĐ';
