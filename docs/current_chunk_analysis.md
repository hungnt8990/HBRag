# Current Chunk Analysis

This note summarizes the chunking/retrieval stack as it exists in the repo after the current hardening pass.

## 1. Current Pipeline

### Ingestion

The backend already supports multiple ingestion paths:

- generic document upload and parsing
- DOffice Elasticsearch ingestion
- docling-based structured parsing
- spreadsheet and slide handling

### Chunking

Current chunking is multi-strategy, not single-mode:

- `recursive`
- `legal_article`
- `table_aware`
- `hybrid_structured`
- `slide_page`
- `heading_aware`
- `docling_router`
- `docling_v6`

DOffice ingestion is normalized first, then converted into structured chunks before indexing.

### Indexing

Indexing is already split into:

- vector indexing in Qdrant
- keyword indexing in PostgreSQL/Elasticsearch paths
- artifact indexing for knowledge artifacts

Artifact-first retrieval is already present in the chat pipeline.

## 2. Profiles In Use

Profiles currently seeded in code/config:

- `legal_admin`
- `catalog_table`
- `staff_technology_matrix`
- `general`
- `spreadsheet`
- `slide`

Important runtime note:

- DOffice documents are stamped internally with `doffice_admin`
- `doffice_admin` is a routing alias, not a true retrieval profile
- the resolver now treats it as content-driven detection instead of falling back to `general`

## 3. Chunk Types Observed

Observed chunk types include:

- `document_summary`
- `document_header`
- `document_body`
- `footer_signature`
- `table_parent`
- `table_row`
- `table_group`
- `table_block`
- `table_title`
- `table_header`
- `entity_profile`
- `entity_summary`
- `heading_section`
- `heading_section_part`
- `slide_page`
- `gis_table`

## 4. Metadata Present Today

The code already carries a rich metadata layer, including:

- document identifiers and text codes
- issued date and issuing org
- chunk type and chunk mode
- page numbers / page ranges
- table ids, titles, headers, row indices
- section titles and heading paths
- staff/person assignment metadata
- enrichment metadata
- access-control payloads

This pass added a few important aliases:

- `source_span`
- `table_title`
- `table_headers`
- `row_index`

## 5. Common Failure Modes

The most relevant failure modes for this repo are:

- chunk metadata is split across several naming conventions
- table titles are sometimes stored as `table_name` and sometimes consumed as `table_title`
- raw table placeholders like `[[TABLE_1]]` can leak into searchable content if not guarded
- DOffice documents use a routing alias (`doffice_admin`) that should not collapse to `general`
- some DOffice/table rows still lack exact character-level source spans because the source parser does not expose them
- a first-class benchmark runner now exists, but richer gold labels are still needed for deeper row/article-level scoring

## 6. What Was Hardened

This pass tightened the following:

- `doffice_admin` now resolves by actual content detection
- placeholder chunks are blocked from indexing and enrichment
- structured chunks preserve `source_span`, `table_title`, and `table_headers`
- DOffice chunk metadata now preserves the same table aliases

## 7. Files Touched

- `backend/app/services/document_profiles.py`
- `backend/app/services/rag_chunk.py`
- `backend/app/services/keyword_search.py`
- `backend/app/services/chunk_enrichment_service.py`
- `backend/app/services/chunking_service.py`
- `backend/app/services/table_aware_chunking.py`
- `backend/app/services/doffice_content_normalizer.py`
- `backend/app/services/doffice_chunking.py`
- `backend/scripts/maintenance/chunking_benchmark.py`
- `backend/tests/test_rag_chunk.py`
- `backend/tests/test_document_profiles.py`
- `backend/tests/test_doffice_ingestion.py`

## 8. Recommended Next Step

Extend the benchmark runner with richer gold labels so it can evaluate:

- document-code lookups
- person/assignment lookups
- table row lookups
- legal clause lookups
- summary questions
