# Final adaptive semantic chunking corrections

This revision keeps the existing adaptive router and changes only the administrative-document path.

## Corrected

- Removed synthetic `Loại:`, `Chủ đề:` and `Mục:` lines from visible/source chunk text.
- Stored document classification and subject only in metadata.
- Kept original table-row content in `raw_text`; semantic key-value content is stored in `normalized_text` / `retrieval_text` / `contextualized_text`.
- Removed the invented `Nguồn: Phụ lục văn bản hành chính` line from embedded content.
- Added consistent `system`, `unit`, `source_stt`, `incident_id`, `incident_sequence`, `source_table_row`, `lead_units` and `coordination_units` metadata.
- Generated stable incident ids such as `2a`, `2b`, `3a` ... without altering the source STT.
- Prevented stale `iii) Web và app CSKH` metadata from leaking into CMIS and remote-metering records.
- Removed duplicate/trailing `Kính gửi:` markers.
- Removed national header/date furniture from directive chunks when Docling reading order moves them into the body.
- Preserved the existing strategy for non-administrative documents and unrelated tables.
- Added regression tests for source-safe text and consistent incident metadata.

## Validation

- `python -m compileall -q app tests`: passed.
- Full `pytest` could not run in the packaging environment because `sqlalchemy` is not installed.
- `ruff` executable is not installed in the packaging environment.
