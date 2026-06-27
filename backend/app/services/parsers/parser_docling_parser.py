from __future__ import annotations

import json
import re
import tempfile
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.parsers.parser_base import DocumentParser, ParsedDocument, ParsedElement


class DoclingParser(DocumentParser):
    """Parse supported office documents into a structured DoclingDocument.

    The serialized Docling document is returned in metadata only as a transient
    ingestion artifact. ``DocumentParserService`` persists it to object storage
    and removes the large object before writing document metadata to PostgreSQL.
    """

    supported_extensions = frozenset({".pdf", ".pptx", ".docx"})
    supported_mime_types = frozenset(
        {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )

    def is_available(self) -> bool:
        try:
            __import__("docling")
        except ImportError:
            return False
        return True

    def is_implemented(self) -> bool:
        return True

    def parse(self, file_content: bytes) -> ParsedDocument:
        return self.parse_document(
            file_content,
            filename="document.pdf",
            mime_type="application/pdf",
        )

    def parse_document(
        self,
        file_content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> ParsedDocument:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        suffix = Path(filename).suffix.lower() or self._suffix_from_mime_type(mime_type)
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        if settings.docling_ocr_mode == "rapidocr-onnx":
            pipeline_options.do_ocr = True
            pipeline_options.force_backend_text = False
            pipeline_options.ocr_options = RapidOcrOptions(
                backend="onnxruntime",
                force_full_page_ocr=False,
            )
        else:
            # Text PDFs should use their embedded text. This avoids unnecessary OCR,
            # is faster, and prevents OCR from changing technical identifiers.
            pipeline_options.do_ocr = False
            pipeline_options.force_backend_text = True

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_file.write(file_content)
                temp_path = temp_file.name
            conversion = converter.convert(source=Path(temp_path))
            doc = conversion.document
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)

        markdown = str(doc.export_to_markdown())
        document_payload = self._document_payload(doc)
        page_texts = self._native_pdf_page_texts(file_content) if suffix == ".pdf" else {}
        return ParsedDocument(
            text=markdown,
            metadata={
                "parser": "docling",
                "parser_version": self._package_version("docling"),
                "docling_document": document_payload,
                "docling_ocr_mode": settings.docling_ocr_mode,
                "document_title": getattr(doc, "name", None),
                "page_texts": page_texts,
            },
            elements=self._parsed_elements(doc),
        )

    @staticmethod
    def _native_pdf_page_texts(file_content: bytes) -> dict[str, str]:
        """Extract the best embedded text per PDF page.

        pdfplumber is preferred for layout-heavy Vietnamese slides, while
        pypdf remains a fallback for pages where it produces more usable text.
        """

        pdfplumber_pages: list[str] = []
        pypdf_pages: list[str] = []
        try:
            import pdfplumber

            with pdfplumber.open(BytesIO(file_content)) as pdf:
                pdfplumber_pages = [
                    (page.extract_text(x_tolerance=1, y_tolerance=3) or "")
                    .replace("\x00", "")
                    .strip()
                    for page in pdf.pages
                ]
        except Exception:
            pdfplumber_pages = []

        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(file_content))
            pypdf_pages = [
                (page.extract_text() or "").replace("\x00", "").strip()
                for page in reader.pages
            ]
        except Exception:
            pypdf_pages = []

        def score(value: str) -> float:
            tokens = value.split()
            if not tokens:
                return -100.0
            single = sum(1 for token in tokens if len(token) == 1 and token.isalpha())
            broken = len(re.findall(r"\b[A-Za-z\u00C0-\u1EF9]{1,3}\s+[\u00C0-\u1EF9]\b", value))
            return float(len(tokens)) - single * 3.0 - broken * 2.5

        count = max(len(pdfplumber_pages), len(pypdf_pages))
        output: dict[str, str] = {}
        for index in range(count):
            plumber = pdfplumber_pages[index] if index < len(pdfplumber_pages) else ""
            pypdf = pypdf_pages[index] if index < len(pypdf_pages) else ""
            output[str(index + 1)] = plumber if score(plumber) >= score(pypdf) else pypdf
        return output

    @staticmethod
    def _suffix_from_mime_type(mime_type: str | None) -> str:
        return {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }.get((mime_type or "").lower(), ".pdf")

    @staticmethod
    def _document_payload(doc: Any) -> dict[str, Any]:
        # Use Docling's own lossless serializer. The paired load_from_json() API
        # is then able to restore provenance, hierarchy, furniture and tables
        # without converting the source file again.
        saver = getattr(doc, "save_as_json", None)
        temp_path: str | None = None
        if callable(saver):
            try:
                with tempfile.NamedTemporaryFile(suffix=".docling.json", delete=False) as file:
                    temp_path = file.name
                saver(filename=Path(temp_path))
                payload = json.loads(Path(temp_path).read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            finally:
                if temp_path is not None:
                    Path(temp_path).unlink(missing_ok=True)

        exporter = getattr(doc, "export_to_dict", None)
        if callable(exporter):
            payload = exporter()
            if isinstance(payload, dict):
                return payload
        raise RuntimeError("DoclingDocument cannot be serialized to lossless JSON.")

    @classmethod
    def _parsed_elements(cls, doc: Any) -> list[ParsedElement]:
        elements: list[ParsedElement] = []
        heading_stack: list[str] = []
        iterator = getattr(doc, "iterate_items", None)
        if not callable(iterator):
            return elements

        for entry in iterator():
            if isinstance(entry, tuple) and len(entry) >= 2:
                item, level = entry[0], entry[1]
            else:
                item, level = entry, 0
            label = cls._label_value(getattr(item, "label", None))
            text = cls._item_text(item, doc)
            if not text.strip():
                continue

            element_type = cls._element_type(label)
            if element_type in {"title", "heading"}:
                depth = max(0, int(level or 0))
                heading_stack = heading_stack[:depth]
                heading_stack.append(text.strip())

            page_number, bbox = cls._item_provenance(item)
            table_id = cls._self_ref(item) if element_type in {"table", "table_row"} else None
            elements.append(
                ParsedElement(
                    element_type=element_type,
                    text=text.strip(),
                    page_number=page_number,
                    section_title=heading_stack[-1] if heading_stack else None,
                    heading_path=list(heading_stack),
                    table_id=table_id,
                    bbox=bbox,
                    metadata={
                        "source": "docling",
                        "docling_label": label,
                        "docling_self_ref": cls._self_ref(item),
                    },
                )
            )
        return elements

    @staticmethod
    def _label_value(label: Any) -> str:
        value = getattr(label, "value", label)
        return str(value or "unknown").lower()

    @staticmethod
    def _element_type(label: str) -> str:
        if label in {"title", "document_index"}:
            return "title"
        if label in {"section_header", "heading"}:
            return "heading"
        if label in {"list_item"}:
            return "list_item"
        if label in {"table"}:
            return "table"
        if label in {"picture", "figure"}:
            return "figure"
        if label in {"code"}:
            return "code"
        if label in {"page_header", "page_footer"}:
            return "unknown"
        return "paragraph"

    @staticmethod
    def _item_text(item: Any, doc: Any) -> str:
        label = DoclingParser._label_value(getattr(item, "label", None))
        if label == "table":
            exporter = getattr(item, "export_to_markdown", None)
            if callable(exporter):
                try:
                    return str(exporter(doc=doc))
                except TypeError:
                    return str(exporter())
        return str(getattr(item, "text", "") or "")

    @staticmethod
    def _item_provenance(
        item: Any,
    ) -> tuple[int | None, tuple[float, float, float, float] | None]:
        provenance = list(getattr(item, "prov", None) or [])
        if not provenance:
            return None, None
        first = provenance[0]
        page_number = getattr(first, "page_no", None)
        bbox_value = getattr(first, "bbox", None)
        if bbox_value is None:
            return page_number if isinstance(page_number, int) else None, None
        left = getattr(bbox_value, "l", None)
        top = getattr(bbox_value, "t", None)
        right = getattr(bbox_value, "r", None)
        bottom = getattr(bbox_value, "b", None)
        if all(isinstance(value, int | float) for value in (left, top, right, bottom)):
            bbox = (float(left), float(top), float(right), float(bottom))
        else:
            bbox = None
        return page_number if isinstance(page_number, int) else None, bbox

    @staticmethod
    def _self_ref(item: Any) -> str | None:
        value = getattr(item, "self_ref", None)
        return str(value) if value is not None else None

    @staticmethod
    def _package_version(package_name: str) -> str | None:
        try:
            return version(package_name)
        except PackageNotFoundError:
            return None
