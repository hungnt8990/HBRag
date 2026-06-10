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


def test_router_selects_table_aware_when_table_element_exists() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(
            filename="report.pdf",
            mime_type="application/pdf",
            parsed_text="Intro without markers",
            parsed_elements=[
                ParsedElement(
                    element_type="table_row",
                    text="A: one | B: two",
                    table_id="t1",
                    row_index=1,
                )
            ],
        )
    )

    assert plan.strategy == "table_aware"
    assert plan.reason == "parsed_table_elements"

def test_router_selects_slide_page_when_page_elements_exist() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(
            filename="slides.pdf",
            mime_type="application/pdf",
            parsed_text="Page one\nPage two",
            parsed_elements=[
                ParsedElement(element_type="page", text="Page one", page_number=1),
                ParsedElement(element_type="page", text="Page two", page_number=2),
            ],
        )
    )

    assert plan.strategy == "slide_page"
    assert plan.reason == "parsed_slide_or_page_elements"

def test_router_selects_heading_aware_when_heading_elements_exist() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(
            filename="notes.md",
            mime_type="text/markdown",
            parsed_text="# Overview\n\nBody",
            parsed_elements=[
                ParsedElement(
                    element_type="heading",
                    text="Overview",
                    section_title="Overview",
                    heading_path=["Overview"],
                )
            ],
        )
    )

    assert plan.strategy == "heading_aware"
    assert plan.reason == "parsed_heading_elements"

def test_chunking_router_prefers_table_aware_over_heading_for_staff_table() -> None:
    text = (
        "GIỚI THIỆU HỆ THỐNG\n"
        "DANH SÁCH NHÂN SỰ PHỤ TRÁCH TỪNG MẢNG CÔNG NGHỆ LÕI\n"
        "STT Mảng công nghệ Phòng chủ trì Nhân sự đề xuất\n"
        "3 Xây dựng nền tảng RAG trên dữ liệu nội bộ PTUD 1. Nguyễn Trọng Hùng\n"
    )

    plan = ChunkingRouter().plan(
        ChunkingRequest(filename="report.pdf", mime_type="application/pdf", parsed_text=text)
    )

    assert plan.strategy == "table_aware"
    assert plan.reason == "staff_area_table_markers"


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

def test_heading_aware_chunks_from_elements_without_merging_sections() -> None:
    elements = [
        ParsedElement(
            element_type="heading",
            text="Overview",
            section_title="Overview",
            heading_path=["Overview"],
        ),
        ParsedElement(
            element_type="paragraph",
            text="Alpha content.",
            section_title="Overview",
            heading_path=["Overview"],
        ),
        ParsedElement(
            element_type="heading",
            text="Details",
            section_title="Details",
            heading_path=["Details"],
        ),
        ParsedElement(
            element_type="paragraph",
            text="Beta content.",
            section_title="Details",
            heading_path=["Details"],
        ),
    ]

    chunks = HeadingAwareChunker(chunk_size=1000).chunk_elements(
        elements,
        "Overview\n\nAlpha content.\n\nDetails\n\nBeta content.",
    )

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


def test_heading_aware_splits_long_section_with_part_metadata() -> None:
    text = "Overview\n" + ("Sentence body. " * 60)

    chunks = HeadingAwareChunker(chunk_size=120, chunk_overlap=20).chunk_text(text)

    assert len(chunks) > 1
    assert all(chunk.metadata["section_title"] == "Overview" for chunk in chunks)
    assert chunks[0].metadata["chunk_type"] == "heading_section_part"
    assert chunks[0].metadata["part_total"] == len(chunks)


def test_requested_slide_page_without_elements_falls_back() -> None:
    plan = ChunkingRouter().plan(
        ChunkingRequest(
            filename="slides.pdf",
            mime_type="application/pdf",
            parsed_text="Slide-like text but no structured elements",
            requested_chunk_mode="slide_page",
        )
    )

    assert plan.strategy == "fallback"
    assert plan.reason == "no_page_or_slide_elements_available"

def test_optional_docling_placeholder_is_not_registered(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_parser_provider", "docling")
    monkeypatch.setattr(settings, "enable_docling", True)
    monkeypatch.setattr(DoclingParser, "is_available", lambda self: True)
    monkeypatch.setattr(DoclingParser, "is_implemented", lambda self: False)

    parsers = build_default_parsers()

    assert not any(isinstance(parser, DoclingParser) for parser in parsers)

def test_optional_unstructured_placeholder_is_not_registered(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_parser_provider", "unstructured")
    monkeypatch.setattr(settings, "enable_unstructured", True)
    monkeypatch.setattr(UnstructuredParser, "is_available", lambda self: True)
    monkeypatch.setattr(UnstructuredParser, "is_implemented", lambda self: False)

    parsers = build_default_parsers()

    assert not any(isinstance(parser, UnstructuredParser) for parser in parsers)


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
