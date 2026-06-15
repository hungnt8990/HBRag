# Docling metadata consistency fix

## Changes

- Table identity is now content-first. `Tên bảng dữ liệu:` overrides stale inherited headings and metadata.
- Small tables that fit in one chunk are emitted as `table_complete` without a synthetic parent.
- Split tables still receive a `table_parent`; grouping is case-insensitive and based on the declared table identity.
- Parent-child table links are validated before IDs are assigned. Invalid links are removed and marked critical.
- Added stale table-state validation for conflicts between content and metadata.
- Parent table summaries now use `Nguồn gốc dữ liệu`; actual external systems exclude manual editing and GIS-generated IDs.
- Added regression tests for the F10 -> HinhAnhCotDien transition, small tables, and content-first parent grouping.

## Validation

- `python -m py_compile app/services/docling_v6_chunking.py`: passed.
- All test functions in `tests/test_docling_v6_repairs.py` were executed directly and passed.
- Full pytest could not run in this sandbox because `sqlalchemy` is not installed.
