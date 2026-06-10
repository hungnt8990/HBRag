import re
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from app.services.parsers.base import DocumentParser, ParsedDocument
from app.services.parsers.table_serialization import (
    infer_headers,
    rewrite_text_with_serialized_tables,
    serialize_table,
)

PDFPLUMBER_TABLE_SETTINGS = (
    {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
    },
    {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
        "min_words_vertical": 1,
        "min_words_horizontal": 1,
    },
)

MIN_TABLE_COHERENCE_SCORE = 1
MIN_PDFPLUMBER_TABLE_ROWS = 2
PDF_RECENT_LINE_WINDOW = 12
PDF_DUPLICATE_CHAR_RATIO = 0.35
PDF_ORPHAN_TOKEN_PENALTY = 20
PDF_ORPHAN_LINE_PENALTY = 80
PDF_DUPLICATE_LINE_PENALTY = 40


class PdfParser(DocumentParser):
    supported_extensions = frozenset({".pdf"})
    supported_mime_types = frozenset({"application/pdf"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        pdfplumber_text = self._parse_with_pdfplumber(file_content)
        if pdfplumber_text is not None:
            return ParsedDocument(text=pdfplumber_text)
        return ParsedDocument(text=self._parse_with_pypdf(file_content))

    def _parse_with_pdfplumber(self, file_content: bytes) -> str | None:
        try:
            import pdfplumber
        except ImportError:
            return None

        try:
            pypdf_pages = self._extract_pypdf_pages(file_content)
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                pages: list[str] = []
                extracted_text = False
                extracted_table = False
                for page_index, page in enumerate(pdf.pages, start=1):
                    page_parts: list[str] = []
                    page_text = self._clean_extracted_text(page.extract_text() or "")
                    pypdf_page_text = (
                        pypdf_pages[page_index - 1]
                        if page_index <= len(pypdf_pages)
                        else ""
                    )
                    page_text = self._choose_better_page_text(page_text, pypdf_page_text)
                    if page_text:
                        extracted_text = True
                        page_parts.append(page_text)

                    for table_index, table in enumerate(
                        self._extract_pdfplumber_tables(page),
                        start=1,
                    ):
                        serialized = serialize_table(
                            table_id=f"pdf_p{page_index}_{table_index}",
                            rows=table,
                            page_number=page_index,
                        )
                        if serialized:
                            extracted_table = True
                            page_parts.append(serialized)

                    if page_parts:
                        pages.append("\n\n".join(page_parts))
        except Exception:
            return None

        if not extracted_text and not extracted_table:
            return None
        return "\n\n".join(page for page in pages if page.strip())

    def _extract_pypdf_pages(self, file_content: bytes) -> list[str]:
        try:
            reader = PdfReader(BytesIO(file_content))
        except Exception:
            return []

        pages: list[str] = []
        for page in reader.pages:
            raw_text = (
                page.extract_text(extraction_mode="layout")
                or page.extract_text()
                or ""
            )
            pages.append(self._clean_extracted_text(raw_text))
        return pages

    def _parse_with_pypdf(self, file_content: bytes) -> str:
        reader = PdfReader(BytesIO(file_content))
        pages: list[str] = []
        for page_index, page in enumerate(reader.pages, start=1):
            raw_text = (
                page.extract_text(extraction_mode="layout")
                or page.extract_text()
                or ""
            )
            if not raw_text.strip():
                continue
            page_text = rewrite_text_with_serialized_tables(
                text=raw_text,
                page_number=page_index,
                table_id_prefix=f"pdf_p{page_index}",
            )
            page_text = self._clean_extracted_text(page_text)
            if page_text:
                pages.append(page_text)
        return "\n\n".join(page for page in pages if page.strip())

    @staticmethod
    def _extract_pdfplumber_tables(page: Any) -> list[list[list[str]]]:
        best_tables: list[list[list[str]]] = []
        best_score: tuple[int, int, int, int] | None = None
        for table_settings in PDFPLUMBER_TABLE_SETTINGS:
            raw_tables = page.extract_tables(table_settings=table_settings) or []
            tables = [
                PdfParser._normalize_pdfplumber_table(table)
                for table in raw_tables
            ]
            tables = [
                table
                for table in tables
                if len(table) >= MIN_PDFPLUMBER_TABLE_ROWS
                and PdfParser._has_inferred_table_header(table)
                and PdfParser._score_extracted_table(table)[0]
                >= MIN_TABLE_COHERENCE_SCORE
            ]
            if not tables:
                continue

            strategy_score = PdfParser._score_table_set(tables)
            if best_score is None or strategy_score > best_score:
                best_score = strategy_score
                best_tables = tables
        return best_tables

    @staticmethod
    def _normalize_pdfplumber_table(table: list[list[Any]]) -> list[list[str]]:
        normalized_rows: list[list[str]] = []
        for row in table:
            normalized_rows.append(
                ["" if cell is None else str(cell) for cell in row]
            )
        return normalized_rows

    @staticmethod
    def _score_extracted_table(table: list[list[str]]) -> tuple[int, int, int, int]:
        if not table:
            return (0, 0, 0, 0)

        width = max((len(row) for row in table), default=0)
        row_count = len(table)
        multi_column_rows = sum(1 for row in table if sum(bool(cell.strip()) for cell in row) > 1)
        non_empty_cells = sum(1 for row in table for cell in row if cell.strip())
        total_chars = sum(len(cell.strip()) for row in table for cell in row if cell.strip())
        total_cells = row_count * width
        empty_cells = (row_count * width) - non_empty_cells
        coherence_score = total_chars - total_cells - (width * width) - empty_cells
        return (coherence_score, total_chars, multi_column_rows, -width)

    @staticmethod
    def _has_inferred_table_header(table: list[list[str]]) -> bool:
        _headers, _data_rows, has_header = infer_headers(table)
        return has_header

    @staticmethod
    def _score_table_set(tables: list[list[list[str]]]) -> tuple[int, int, int, int]:
        scores = [PdfParser._score_extracted_table(table) for table in tables]
        return (
            sum(score[0] for score in scores),
            max((score[1] for score in scores), default=0),
            sum(score[2] for score in scores),
            sum(score[3] for score in scores),
        )

    @staticmethod
    def _clean_extracted_text(text: str) -> str:
        """Remove common PDF presentation artifacts without summarizing content."""
        lines: list[str] = []
        recent_normalized: list[str] = []
        last_was_blank = False

        for raw_line in text.replace("\x00", "").splitlines():
            line = " ".join(raw_line.split())
            if not line:
                if lines and not last_was_blank:
                    lines.append("")
                    last_was_blank = True
                continue

            normalized = PdfParser._normalize_line_for_comparison(line)
            collapsed_normalized = PdfParser._normalize_line_for_comparison(
                PdfParser._collapse_repeated_chars(line)
            )
            if normalized in recent_normalized or collapsed_normalized in recent_normalized:
                continue
            if PdfParser._duplicate_char_ratio(line) >= PDF_DUPLICATE_CHAR_RATIO:
                continue

            lines.append(line)
            last_was_blank = False
            recent_normalized.append(normalized)
            if len(recent_normalized) > PDF_RECENT_LINE_WINDOW:
                recent_normalized.pop(0)

        return "\n".join(lines).strip()

    @staticmethod
    def _choose_better_page_text(primary_text: str, fallback_text: str) -> str:
        if not primary_text:
            return fallback_text
        if not fallback_text:
            return primary_text
        primary_orphans, primary_duplicates = PdfParser._text_issue_counts(primary_text)
        fallback_orphans, fallback_duplicates = PdfParser._text_issue_counts(fallback_text)
        if (
            primary_orphans > fallback_orphans
            and fallback_duplicates <= primary_duplicates
            and PdfParser._text_quality_score(fallback_text)
            > PdfParser._text_quality_score(primary_text)
        ):
            return fallback_text
        return primary_text

    @staticmethod
    def _text_issue_counts(text: str) -> tuple[int, int]:
        orphan_count = 0
        duplicate_count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if PdfParser._is_orphan_diacritic_line(tokens):
                orphan_count += len(tokens) + 1
            orphan_count += sum(
                1 for token in tokens if PdfParser._is_orphan_text_token(token)
            )
            if PdfParser._duplicate_char_ratio(line) >= PDF_DUPLICATE_CHAR_RATIO:
                duplicate_count += 1
        return orphan_count, duplicate_count

    @staticmethod
    def _text_quality_score(text: str) -> int:
        score = sum(1 for char in text if char.isalnum())
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            tokens = line.split()
            if PdfParser._is_orphan_diacritic_line(tokens):
                score -= PDF_ORPHAN_LINE_PENALTY
            score -= sum(
                PDF_ORPHAN_TOKEN_PENALTY
                for token in tokens
                if PdfParser._is_orphan_text_token(token)
            )
            if PdfParser._duplicate_char_ratio(line) >= PDF_DUPLICATE_CHAR_RATIO:
                score -= PDF_DUPLICATE_LINE_PENALTY
        return score

    @staticmethod
    def _is_orphan_diacritic_line(tokens: list[str]) -> bool:
        return bool(tokens) and len(tokens) <= 3 and all(
            PdfParser._is_orphan_text_token(token) and not token.isascii()
            for token in tokens
        )

    @staticmethod
    def _is_orphan_text_token(token: str) -> bool:
        stripped = token.strip(".,:;!?()[]{}\"'")
        return len(stripped) == 1 and stripped.isalpha()

    @staticmethod
    def _collapse_repeated_chars(text: str) -> str:
        return re.sub(r"(.)\1+", r"\1", text)

    @staticmethod
    def _normalize_line_for_comparison(text: str) -> str:
        return " ".join(text.split()).casefold()

    @staticmethod
    def _duplicate_char_ratio(text: str) -> float:
        compact = "".join(text.split())
        if len(compact) < 8:
            return 0.0
        duplicate_pairs = sum(
            1 for left, right in zip(compact, compact[1:], strict=False) if left == right
        )
        return duplicate_pairs / max(1, len(compact) - 1)
