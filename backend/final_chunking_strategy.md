# Final Chunking Strategy

## Architecture

The production strategy is artifact-first and evidence-backed:

1. Normalize text with NFC, whitespace, line-break, and dash cleanup.
2. Extract authoritative document metadata from source fields and text.
3. Route chunking adaptively by source/profile/content:
   - DOffice/admin: structure-aware with recursive fallback.
   - Tables: row-level chunks with document/table/header/row context.
   - Legal/regulation: article/clause-aware chunks.
   - Reports: section chunks with recursive fallback.
   - Spreadsheet: table-aware.
   - Slides: slide/page-aware.
4. Run quality gate before save/index.
5. Compile knowledge artifacts from saved chunks.
6. Index artifacts first, then evidence/raw fallback chunks.

## Chunk Types

- `document_summary`
- `document_header`
- `document_preamble`
- `document_section`
- `evidence_chunk`
- `legal_clause`
- `table_parent`
- `table_row`
- `table_group`
- `footer_signature`

## Artifact Types

- `document_summary_artifact`
- `identifier_lookup`
- `table_evidence_artifact`
- `assignment_artifact`
- `legal_evidence_artifact`
- Existing backward-compatible aliases remain: `document_profile`, `table_row_artifact`, `person_assignment_artifact`, `policy_rule_artifact`, `procedure_artifact`.

## Quality Gate

Pre-save/index checks now cover:

- table placeholders -> `indexable=false`, `embedding_enabled=false`
- table rows without title/header/row index -> not indexable
- legal chunks without article context -> not indexable
- DOffice chunks missing document code -> warning/failure metadata
- footer/signature remains non-embedding by default

## Re-ingest/Re-index

Run re-ingest for affected documents so new metadata/artifacts are generated, then index artifacts and chunks. The queue path does artifact indexing before chunk indexing.

Example:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_adaptive_chunking.py tests/test_doffice_ingestion.py tests/test_knowledge_artifact_compiler.py
python scripts/maintenance/chunking_benchmark.py --mode retrieval --limit 25
```

## SQL Validation

```sql
select count(*)
from chunks
where content ~ '\\[\\[TABLE_[0-9]+\\]\\]'
and coalesce((chunk_metadata->>'indexable')::boolean, true) = true;

select count(*)
from chunks
where chunk_metadata->>'chunk_type' = 'table_row'
and (
  chunk_metadata->>'table_title' is null
  or chunk_metadata->>'table_headers' is null
  or chunk_metadata->>'row_index' is null
);

select count(*)
from chunks
where chunk_metadata->>'chunk_type' = 'legal_clause'
and chunk_metadata->>'article_number' is null
and content ilike '%Khoản%';

select count(*)
from chunks
where chunk_metadata->>'chunk_type' = 'footer_signature'
and content ilike '%Phụ lục%';

select chunk_metadata->>'chunk_type' as chunk_type, count(*)
from chunks
group by chunk_metadata->>'chunk_type'
order by count(*) desc;
```

## Known Limitations

- Legal clause detection is deterministic regex-based; unusual OCR headings may still need profile rules.
- Continued physical table merge uses deterministic logical IDs from title/header. Tables with changed headers across pages may need a stronger table-continuation detector.
- LLM enrichment remains optional and is not required for chunking correctness.
