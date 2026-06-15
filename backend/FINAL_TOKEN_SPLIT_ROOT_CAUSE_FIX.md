# Final token split root-cause fix

The quality gate and final splitter now use the exact same RegexVietnameseTokenizer.

Key changes:
- Added a guaranteed hard token split using regex token spans.
- Reworked enforce_token_limit into a queue-based iterative guard.
- Added a final guard after table-parent creation and metadata finalization.
- Added another final guard immediately before build_quality_report.
- Added a regression test for a single oversized Markdown table row.

Runtime verification marker:
Search for `Last line of defence: quality gate and splitter use the exact same tokenizer.`
in `app/services/docling_v6_chunking.py`.
