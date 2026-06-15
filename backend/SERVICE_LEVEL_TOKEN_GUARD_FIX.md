# Service-level token guard fix

## Root cause

The Docling pipeline could still return a stale quality report that referenced
records before the final split. The strict quality gate in `ChunkingService`
trusted that report directly, so the same oversized chunks (`449` and `400`
tokens) kept rejecting the document.

## Fix

`ChunkingService._chunk_document_with_docling_v6()` now performs a final,
defensive pass immediately after `chunk_docling_document()` returns and
immediately before strict quality validation:

1. Re-run `enforce_token_limit()` on `result.records`.
2. Reindex the resulting records.
3. Rebuild the quality report from the guarded records.
4. Replace the original result with a new `DoclingV6ChunkingResult`.
5. Raise a dedicated `Service-level token guard failed` error if any record is
   still over the configured limit.

This makes it impossible for the strict quality gate to reject using an old
quality report or an unsplit record list.

## Validation performed

- `python -m compileall -q app tests`: passed.
- Modified Python files contain no lines over 100 characters.
