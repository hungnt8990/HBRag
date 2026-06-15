# Pytest compatibility fix after removing ChunkingRouter

This patch keeps the router files removed while restoring direct, backward-compatible chunk dispatch inside `ChunkingService`.

## Restored behavior

- Automatic document profile detection (`auto` -> `legal_admin`, `general`, etc.).
- Profile-driven defaults for chunk mode, size, and overlap.
- Explicit `legal_article`, `slide_page`, `heading_aware`, `table_aware`, and `hybrid_structured` modes.
- Automatic structured-element handling when parsed table rows are available.
- Table-row and person entity-profile chunk generation.
- Prose preservation alongside structured table rows.
- Compatibility metadata such as `chunk_strategy` and `router_reason`, without reinstating a router class.

## Architecture

- Docling documents still use the Docling-first pipeline.
- Non-Docling documents are dispatched directly in `ChunkingService`.
- `chunking_router.py`, `segment_router.py`, and router tests remain deleted.
