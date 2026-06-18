from types import SimpleNamespace
from uuid import UUID

from app.services.rag_chunk import (
    RagChunk,
    build_embedding_text,
    qdrant_payload,
    rag_chunk_from_database,
    rag_chunk_from_record,
    should_index_chunk,
    stable_point_id,
)


def _chunk(**overrides) -> RagChunk:
    data = {
        "chunk_id": "chunk_002",
        "document_id": "doc-1",
        "document_version": "v1",
        "tenant_id": "tenant-1",
        "chunk_type": "assignment_section",
        "content_format": "text",
        "text": "1.1. GIS 110kV, GIS trung thế:\n- Hiệu chỉnh PMISToGIS.",
        "section_path": ["1. CPCIT", "1.1. GIS 110kV, GIS trung thế"],
        "unit": "CPCIT",
        "scope": ["GIS 110kV", "GIS trung thế"],
        "pages": [1],
        "page_start": 1,
        "page_end": 1,
        "source_file": "6515.pdf",
        "document_title": "Kế hoạch xây dựng hệ thống GIS EVNCPC",
        "parser": "docling",
        "chunker": "docling_hybrid_v6",
        "chunker_version": "6",
        "quality_status": "pass",
        "indexable": True,
        "embedding_enabled": True,
        "content_hash": "hash",
        "database_chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    data.update(overrides)
    return RagChunk(**data)


def test_build_embedding_text_adds_useful_metadata_without_repeating_heading() -> None:
    chunk = _chunk()

    text = build_embedding_text(chunk)

    assert "Tài liệu: Kế hoạch xây dựng hệ thống GIS EVNCPC" in text
    assert "Đơn vị: CPCIT" in text
    assert text.endswith(chunk.text)
    assert text.count("1.1. GIS 110kV, GIS trung thế") == 1


def test_build_embedding_text_does_not_repeat_document_title_already_in_text() -> None:
    title = "Kế hoạch xây dựng hệ thống GIS EVNCPC"
    chunk = _chunk(text=f"{title}\nNội dung", document_title=title, section_path=[])

    text = build_embedding_text(chunk)

    assert text.count(title) == 1

def test_build_embedding_text_orders_rule_context_original_and_llm_enrichment() -> None:
    chunk = _chunk(
        text="Nội dung gốc để citation.",
        embedding_text="LLM enrichment:\nTóm tắt: Bản làm giàu ngắn.",
        enriched=True,
        rule_enrichment={
            "document_title": "Quyết định vận hành",
            "document_code": "123/QĐ-CPCIT",
            "section_path": "Chương I > Điều 1",
        },
    )

    text = build_embedding_text(chunk)

    assert "Tài liệu: Quyết định vận hành" in text
    assert "Số hiệu/mã: 123/QĐ-CPCIT" in text
    assert "Nội dung gốc để citation." in text
    assert "LLM enrichment:" in text
    assert text.index("Tài liệu: Quyết định vận hành") < text.index("Nội dung gốc")
    assert text.index("Nội dung gốc") < text.index("LLM enrichment:")


def test_footer_and_disabled_chunks_are_not_indexable() -> None:
    assert should_index_chunk(_chunk(chunk_type="administrative_footer")) is False
    assert should_index_chunk(_chunk(indexable=False)) is False
    assert should_index_chunk(_chunk(embedding_enabled=False)) is False
    assert should_index_chunk(_chunk(quality_status="rejected")) is False


def test_docling_record_maps_headings_pages_and_table_metadata() -> None:
    record = {
        "chunk_id": "chunk_015",
        "chunk_type": "docling_hybrid_repaired",
        "headings": [
            "2. Chi tiết dữ liệu chuyển đổi",
            "(1) F08_CotDien_HT - Lớp cột điện",
        ],
        "pages": [6, 5],
        "text": (
            "(1) F08_CotDien_HT - Lớp cột điện\n"
            "| TT | Trường dữ liệu | Mô tả |\n"
            "|---|---|---|\n"
            "| 17 | DonViQuanLy | Đơn vị quản lý |\n"
            "| 18 | TinhThanh | Tỉnh thành |"
        ),
    }

    chunk = rag_chunk_from_record(
        record,
        document_id="doc",
        source_file="6515.pdf",
        source_uri="documents/doc/original.pdf",
        document_title="GIS",
        document_version="v1",
        tenant_id="tenant",
        parser="docling",
        parser_version="2.x",
        chunker="docling_hybrid_v6",
        chunker_version="6",
        chunk_index=14,
    )

    assert chunk.section_path[-1].startswith("(1) F08_CotDien_HT")
    assert chunk.pages == [5, 6]
    assert chunk.page_start == 5
    assert chunk.page_end == 6
    assert chunk.content_format == "markdown_table"
    assert chunk.chunk_type == "table_rows"
    assert chunk.table_name == "F08_CotDien_HT"
    assert chunk.table_description == "Lớp cột điện"
    assert chunk.row_start == 17
    assert chunk.row_end == 18
    assert chunk.table_columns == ["TT", "Trường dữ liệu", "Mô tả"]


def test_docling_staff_table_row_recovers_relationship_metadata() -> None:
    record = {
        "chunk_id": "chunk_020",
        "chunk_type": "table_row",
        "pages": [5],
        "text": (
            "STT: 3\n"
            "Mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Phòng chủ trì: PTUD\n"
            "Nhân sự đề xuất: Tống Phước Lâm; Nguyễn Quang Lâm; "
            "Nguyễn Trọng Hùng; Võ Văn Hòa; Đoàn Gia Hy (kiểm thử)"
        ),
    }

    chunk = rag_chunk_from_record(
        record,
        document_id="doc",
        source_file="Danh-Sach-Nhan-Su-Cac-Mang-Cong-Nghe-Loi.pdf",
        source_uri="documents/doc/original.pdf",
        document_title="Danh sách nhân sự",
        document_version="v1",
        tenant_id="tenant",
        parser="docling",
        parser_version="2.x",
        chunker="docling_hybrid_v6",
        chunker_version="6",
        chunk_index=19,
    )

    assert chunk.relationship_type == "technology_area_staff"
    assert chunk.area == "Xây dựng nền tảng RAG trên dữ liệu nội bộ"
    assert chunk.lead_department == "PTUD"
    assert "Nguyễn Trọng Hùng" in chunk.staff_names
    payload = qdrant_payload(chunk)
    assert payload["relationship_type"] == "technology_area_staff"
    assert payload["staff_names"] == [
        "Tống Phước Lâm",
        "Nguyễn Quang Lâm",
        "Nguyễn Trọng Hùng",
        "Võ Văn Hòa",
        "Đoàn Gia Hy",
    ]


def test_docling_staff_entity_profile_recovers_person_metadata() -> None:
    record = {
        "chunk_id": "chunk_033",
        "chunk_type": "entity_profile",
        "pages": [5],
        "text": (
            "Nhân sự: Nguyễn Trọng Hùng.\n"
            "Nguyễn Trọng Hùng được đề xuất tham gia các mảng công nghệ:\n"
            "- Xây dựng nền tảng RAG trên dữ liệu nội bộ; phòng chủ trì: PTUD.\n"
            "- Kho dữ liệu AI dùng chung; phòng chủ trì: VH.\n"
            "- Platform AI; phòng chủ trì: PTUD.\n"
            "Nguyễn Trọng Hùng được đề xuất tham gia 03 mảng công nghệ: "
            "Xây dựng nền tảng RAG trên dữ liệu nội bộ, Kho dữ liệu AI dùng chung "
            "và Platform AI."
        ),
    }

    chunk = rag_chunk_from_record(
        record,
        document_id="doc",
        source_file="Danh-Sach-Nhan-Su-Cac-Mang-Cong-Nghe-Loi.pdf",
        source_uri="documents/doc/original.pdf",
        document_title="Danh sách nhân sự",
        document_version="v1",
        tenant_id="tenant",
        parser="docling",
        parser_version="2.x",
        chunker="docling_hybrid_v6",
        chunker_version="6",
        chunk_index=32,
    )

    assert chunk.relationship_type == "technology_area_staff"
    assert chunk.person_name == "Nguyễn Trọng Hùng"
    assert len(chunk.areas) == 3
    assert chunk.answer_text == (
        "Nguyễn Trọng Hùng được đề xuất tham gia 03 mảng công nghệ: "
        "Xây dựng nền tảng RAG trên dữ liệu nội bộ, Kho dữ liệu AI dùng chung và Platform AI."
    )
    embedding_text = build_embedding_text(chunk)
    assert "Hồ sơ nhân sự: Nguyễn Trọng Hùng" in embedding_text
    assert "Các mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ - PTUD" in embedding_text


def test_qdrant_payload_keeps_clean_text_and_database_chunk_id() -> None:
    chunk = _chunk(raw_text="raw")

    payload = qdrant_payload(chunk, store_raw_text=False)

    assert payload["text"] == chunk.text
    assert payload["chunk_id"] == chunk.database_chunk_id
    assert payload["semantic_chunk_id"] == "chunk_002"
    assert "raw_text" not in payload


def test_stable_point_id_is_deterministic_and_content_sensitive() -> None:
    first = _chunk()
    second = _chunk()
    changed = _chunk(content_hash="changed")

    assert stable_point_id(first) == stable_point_id(second)
    assert stable_point_id(first) != stable_point_id(changed)


def test_database_chunk_mapping_preserves_db_id_for_retrieval_hydration() -> None:
    db_chunk_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    document_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    db_chunk = SimpleNamespace(
        id=db_chunk_id,
        document_id=document_id,
        chunk_index=2,
        content="Nội dung",
        token_count=12,
        chunk_metadata={
            "chunk_id": "chunk_003",
            "headings": ["1. CPCIT", "1.2. GIS hạ thế"],
            "unit": "CPCIT",
            "scope": ["GIS hạ thế"],
            "pages": [1, 2],
            "indexable": True,
            "embedding_enabled": True,
        },
    )
    document = SimpleNamespace(
        id=document_id,
        title="GIS",
        organization_id=None,
        knowledge_base_id=None,
        uploaded_by_user_id=None,
        visibility="global",
        document_metadata={
            "parser": "docling",
            "parsed_metadata": {"parser_version": "2.x"},
            "document_version": "v1",
        },
    )

    chunk = rag_chunk_from_database(
        db_chunk,
        document=document,
        source_file="6515.pdf",
        source_uri="documents/6515.pdf",
    )

    assert chunk.database_chunk_id == str(db_chunk_id)
    assert chunk.chunk_id == "chunk_003"
    assert chunk.page_start == 1
    assert chunk.page_end == 2
