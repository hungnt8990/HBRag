from app.core.config import settings
from app.services.chunking_router import (
    ChunkingRequest,
    ChunkingRouter,
    HeadingAwareChunker,
    SlidePageChunker,
)
from app.services.document_parser_service import build_default_parsers
from app.services.parsers import ParsedElement
from app.services.parsers.optional_adapters import DoclingParser, UnstructuredParser


def test_router_selects_table_aware_when_table_row_exists() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(
            filename="report.pdf",
            mime_type="application/pdf",
            parsed_text="Intro\nTABLE_ROW table_id=t1 row=1 | A: one",
        )
    )

    assert plan.strategy == "table_aware"
    assert plan.reason == "table_markers_or_pipe_table"


def test_router_selects_legal_article_for_vietnamese_articles() -> None:
    text = "CHƯƠNG I\nĐiều 1. Phạm vi\nNội dung\nĐiều 2. Đối tượng\nNội dung"

    plan = ChunkingRouter().plan(
        ChunkingRequest(filename="policy.pdf", mime_type="application/pdf", parsed_text=text)
    )

    assert plan.strategy == "legal_article"


def test_router_selects_recursive_fallback() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(filename="note.txt", mime_type="text/plain", parsed_text="plain text")
    )

    assert plan.strategy == "recursive"
    assert plan.reason == "document_profile_default"


def test_heading_aware_keeps_section_boundaries() -> None:
    text = "Overview\nAlpha content.\nDetails\nBeta content."

    chunks = HeadingAwareChunker(chunk_size=1000).chunk_text(text)

    assert len(chunks) == 2
    assert chunks[0].metadata["section_title"] == "Overview"
    assert "Details" not in chunks[0].content
    assert chunks[1].metadata["heading_path"] == ["Details"]


def test_slide_page_chunker_preserves_page_metadata() -> None:
    elements = [
        ParsedElement(element_type="slide", text="Slide 1 text", page_number=1),
        ParsedElement(element_type="slide", text="Slide 2 text", page_number=2),
    ]

    chunks = SlidePageChunker().chunk_elements(elements, "Slide 1 text\nSlide 2 text")

    assert [chunk.metadata["page_range"] for chunk in chunks] == [[1, 1], [2, 2]]
    assert all(chunk.metadata["chunk_type"] == "slide" for chunk in chunks)


def test_optional_docling_missing_dependency_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_parser_provider", "docling")
    monkeypatch.setattr(settings, "enable_docling", True)
    monkeypatch.setattr(DoclingParser, "is_available", lambda self: False)

    parsers = build_default_parsers()

    assert not any(isinstance(parser, DoclingParser) for parser in parsers)
    assert any(parser.supports(filename="sample.pdf", mime_type=None) for parser in parsers)


def test_optional_unstructured_missing_dependency_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_parser_provider", "unstructured")
    monkeypatch.setattr(settings, "enable_unstructured", True)
    monkeypatch.setattr(UnstructuredParser, "is_available", lambda self: False)

    parsers = build_default_parsers()

    assert not any(isinstance(parser, UnstructuredParser) for parser in parsers)
    assert any(parser.supports(filename="sample.docx", mime_type=None) for parser in parsers)
