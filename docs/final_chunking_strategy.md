# Final Chunking Strategy

## Recommendation

Use an adaptive, artifact-first strategy.

### Priority Order

1. knowledge artifact / QA packet
2. evidence chunk
3. table row / legal clause chunk
4. raw fallback chunk

## Strategy by Document Type

- administrative / DOffice text: `structure-aware` with recursive fallback
- tables: `table-aware` row-level chunks with header and title context
- legal/regulatory text: `legal clause-aware` chunks that keep article/clause context
- long reports / explanations: semantic section chunks with recursive fallback
- mixed documents: hybrid router based on source type, detected structure, and metadata

## Metadata Rules

Keep a consistent metadata contract across all chunking paths:

- `doc_id`
- `doc_code`
- `issued_date`
- `issuing_org`
- `document_type`
- `document_title`
- `chunk_type`
- `chunk_mode`
- `source_span`
- `page_number` / `page_range`
- `section_title`
- `table_title`
- `table_headers`
- `row_index`

## Guardrails

- Never embed raw table placeholders.
- Never let footer/signature text absorb appendix content.
- Never let a table row lose its header/title context.
- Never allow `doffice_admin` to behave like a real retrieval profile.
- Prefer deterministic parsing and routing over LLM heuristics.

## Existing Strengths

The repo already has:

- DOffice normalization
- table-aware chunking
- legal chunking
- artifact compilation
- hybrid retrieval
- reranking
- query contracts

## Remaining Work

The main remaining work is operational rather than architectural:

- extend the benchmark runner with richer gold labels for row/article-level scoring
- emit retrieval/answer reports from real corpora
- add regression fixtures for more DOffice variants
- add stricter quality gates for source-span completeness where the parser supports it
