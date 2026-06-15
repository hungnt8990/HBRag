# Docling slide text quality fix

## Main changes

- Prefer the best native PDF page text between pdfplumber and pypdf.
- Use a conservative Vietnamese PDF normalizer; do not globally concatenate tokens.
- Repair safe glyph-splitting cases such as `c ứ`, `L ớ p`, `thi ế t`, while preserving real boundaries such as `vb số`, `GIS hạ thế`, and `ban đầu`.
- Score native and Docling page text and select the cleaner source per slide.
- Preserve slide page boundaries.
- Detect table-of-contents slides and mark them non-indexable.
- Detect DEMO/section-divider slides and mark them non-indexable.
- Deduplicate repeated slide titles.
- Reconstruct three-column data-flow slides into source -> processing -> target semantics.
- Reconstruct numbered screenshot callouts into ordered items 1..N.
- Add `text_source` metadata (`native_pdf` or `docling`).
- Add fragmented-text quality warnings and prevent low-quality Docling-only text from embedding.
- Build document context from the cleaner first-page native text.

## Validation performed

- `python -m compileall -q app tests`
- Verified no modified Python line exceeds 100 characters.
- Verified native PDF extraction selects clean pdfplumber text for the supplied GIS slide deck.
