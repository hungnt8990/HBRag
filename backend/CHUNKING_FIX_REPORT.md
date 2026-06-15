# Chunking V6 repair report

## Fixed

- Resets stale schema-table context when moving from `F10_TuPhanPhoi_HT` to `HinhAnhCotDien` and the other attribute tables.
- Makes explicit `Tên bảng dữ liệu:` metadata authoritative over inherited Docling headings.
- Synchronizes final `raw_text` with the actual chunk and preserves untouched parser text in `source_raw_text`.
- Adds semantic validation for table/content/path mismatches and per-chunk quality status.
- Rebalances table chunks to avoid very small final chunks.
- Adds table parent chunks and `parent_chunk_id` links for multi-part schema tables.
- Extracts `field_names`, `source_systems`, and `convertible_fields`.
- Resolves cross-references such as `theo mục 2.2` into retrieval metadata.
- Classifies relationship tables and extracts source/target keys and cardinality.
- Prevents failed chunks (`quality_status=fail`) from being indexed.

## Verification

- `pytest -q`: **241 passed**, 2 non-failing Qdrant compatibility warnings.
- `ruff check` on all modified files: **passed**.
- Added 7 regression tests in `tests/test_docling_v6_repairs.py`.
