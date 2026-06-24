# Current Chunk Analysis

## 1. Current Pipeline

The backend currently has three ingestion paths:

1. Standard upload/re-ingest:
   upload -> parse -> profile detection -> chunk -> compile knowledge artifacts -> optional enrichment -> index.
2. DOffice ingestion:
   fetch Elasticsearch source -> normalize HTML/text/table content -> create document -> DOffice chunking -> optional enrichment -> vector/keyword index.
3. Docling parsed documents:
   parsed Docling artifact -> docling router -> quality report/token guard -> chunk save -> index.

Chunks are saved through `DocumentRepository.create_chunks`, then converted to `RagChunk` for Qdrant/Elasticsearch indexing. Knowledge artifacts already exist (`KnowledgeArtifactCompiler`, `KnowledgeArtifactIndexingService`, `ArtifactFirstRetrievalService`) but DOffice did not compile/index artifacts before this change.

## 2. Profiles In Use

Configured profile names are loaded from `app/services/ingestion_profiles.py` and optionally overridden from DB:

- `legal_admin`: used for legal/article heading chunking.
- `catalog_table`: table-aware catalog documents.
- `staff_technology_matrix`: table-aware people/technology matrix.
- `general`: recursive fallback.
- `spreadsheet`: table-aware.
- `slide`: slide/page-aware.
- `doffice_admin` and `official_document_structured`: routing aliases resolved by content detection, not standalone retrieval profiles.

## 3. Existing Chunk Types

Observed chunk types include:

- `document_summary`
- `document_header`
- `document_body`
- `table_parent`
- `table_row`
- `table_group`
- `footer_signature`
- `gis_table`
- Docling/table-specific chunk types such as `table_rows`, `table_parent`, `schema_field_row`.

This change adds/normalizes:

- `document_preamble`
- `document_section`
- `evidence_chunk`
- `legal_clause`

## 4. Existing Metadata

Existing metadata already includes document fields (`id_vb`, `ky_hieu`, `issued_date`, `issuer`, `doc_codes`, `identifiers`), table hints (`table_name`, `row_index`, feature fields), and access metadata. Gaps found:

- DOffice table rows did not consistently keep `row_cells`, `logical_table_id`, `table_id`, `table_kind`, `row_key`.
- DOffice body chunks were mostly coarse `document_body`.
- DOffice artifact compile/index was not part of the DOffice ingestion path.
- Some important metadata needed explicit allowlist entries before save/index.

## 5. Common Issues Found

- Chunk may lose document title/code/date/org when generated outside the DOffice header path.
- Coarse body chunks can cut across sections or legal articles.
- Table row metadata had header/title fixes, but row cell mapping and logical table identity were missing.
- DOffice footer/signature was separated, but quality gate was not centralized.
- Raw table placeholders were blocked in some indexing paths, but not as a shared pre-save quality policy.
- Artifact-first retrieval existed but DOffice ingestion skipped artifact compilation.
- Normal upload indexed raw chunks before artifacts.

## 6. Proposed Architecture

Target architecture:

DOffice/document source -> normalize -> metadata extraction -> adaptive router -> structure/legal/table evidence chunks -> quality gate -> artifact compiler -> artifact vector index -> raw/evidence chunk index fallback -> hybrid/rerank answer with citations.

The implementation keeps existing profile behavior and adds adaptive DOffice body splitting plus shared metadata/quality checks.

## 7. Files To Change

- `app/services/adaptive_chunking.py`
- `app/services/doffice_chunking.py`
- `app/services/doffice_content_normalizer.py`
- `app/services/doffice_ingestion_service.py`
- `app/services/ingestion_queue.py`
- `app/services/knowledge_artifact_compiler.py`
- `app/services/query_contract_service.py`
- `app/models/knowledge_artifact.py`
- benchmark docs and regression tests
