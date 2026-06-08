"""Tests for generic table-aware chunking.

No column names, table names, person names, or domain logic is hard-coded
in the chunking implementation. These tests verify the system works with
arbitrary table schemas.
"""

from app.services.table_aware_chunking import (
    build_entity_index,
    detect_tables_in_text,
    extract_entities_from_text,
    generate_entity_summary_chunks,
    table_aware_chunk_text,
    table_to_row_chunks,
)

VIETNAMESE_TABLE = (
    "STT | Nội dung | Đơn vị | Người thực hiện\n"
    "1 | Xây dựng hệ thống | Phòng CNTT | Nguyễn Quang Lâm\n"
    "2 | Quản lý dữ liệu | Phòng CNTT | Nguyễn Quang Lâm\n"
    "3 | Đào tạo nhân sự | Phòng Nhân sự | Trần Văn An\n"
    "4 | Bảo trì hạ tầng | Phòng CNTT | Nguyễn Quang Lâm\n"
    "5 | Kiểm tra chất lượng | Phòng QA | Nguyễn Quang Lâm\n"
)

ENGLISH_TABLE = (
    "No | Project | Owner | Members | Note\n"
    "1 | Cloud Migration | Team Alpha | John Smith | Q1\n"
    "2 | Data Platform | Team Beta | Jane Doe | Q2\n"
    "3 | Security Audit | Team Alpha | John Smith | Q3\n"
    "4 | ML Pipeline | Team Gamma | Alice Wong | Q4\n"
)


def test_pipe_table_detection() -> None:
    tables = detect_tables_in_text(VIETNAMESE_TABLE)
    assert len(tables) >= 1
    table = tables[0]
    assert len(table.headers) == 4
    assert len(table.rows) >= 4


def test_row_chunks_contain_all_columns_vietnamese() -> None:
    """Test 1: Vietnamese table produces row chunks with all 4 columns."""
    tables = detect_tables_in_text(VIETNAMESE_TABLE)
    assert tables
    chunks = table_to_row_chunks(tables[0])
    assert len(chunks) >= 4

    first = chunks[0]
    assert first["metadata"]["chunk_type"] == "table_row"
    assert first["metadata"]["chunk_mode"] == "table_aware"
    # All 4 header names should appear in chunk content.
    for header in tables[0].headers:
        assert any(header in c["content"] for c in chunks)


def test_row_chunks_english_table() -> None:
    """Test 2: English table produces row chunks correctly, not dependent on Vietnamese."""
    tables = detect_tables_in_text(ENGLISH_TABLE)
    assert tables
    chunks = table_to_row_chunks(tables[0])
    assert len(chunks) >= 3

    first = chunks[0]
    assert first["metadata"]["chunk_type"] == "table_row"
    headers = first["metadata"]["headers"]
    assert "Project" in headers or "No" in headers


def test_entity_summary_for_multi_row_entity() -> None:
    """Test 3: Entity appearing in multiple rows gets an entity_summary."""
    tables = detect_tables_in_text(VIETNAMESE_TABLE)
    row_chunks = table_to_row_chunks(tables[0])
    entity_index = build_entity_index(row_chunks)
    summaries = generate_entity_summary_chunks(row_chunks, entity_index)

    # Nguyễn Quang Lâm appears in 4 rows → should get a summary.
    lam_summaries = [
        s for s in summaries
        if s["metadata"].get("entity_name") == "Nguyễn Quang Lâm"
    ]
    assert len(lam_summaries) >= 1
    summary = lam_summaries[0]
    assert summary["metadata"]["chunk_type"] == "entity_summary"
    assert summary["metadata"]["entity_name"] == "Nguyễn Quang Lâm"
    assert summary["metadata"]["row_count"] >= 4


def test_entity_retrieval_finds_all_related_rows() -> None:
    """Test 4: Query for entity retrieves all rows containing that entity."""
    all_chunks, entity_index = table_aware_chunk_text(VIETNAMESE_TABLE)

    # Simulate entity lookup.
    assert "Nguyễn Quang Lâm" in entity_index.entities
    related_indices = entity_index.entities["Nguyễn Quang Lâm"]
    assert len(related_indices) >= 4

    # The entity_summary chunk must exist.
    summaries = [
        c for c in all_chunks
        if c.get("metadata", {}).get("entity_name") == "Nguyễn Quang Lâm"
    ]
    assert summaries
    # Summary contains all related tasks.
    summary_text = summaries[0]["content"]
    assert "Xây dựng hệ thống" in summary_text
    assert "Quản lý dữ liệu" in summary_text
    assert "Bảo trì hạ tầng" in summary_text
    assert "Kiểm tra chất lượng" in summary_text


def test_generic_entity_extraction_no_hardcoding() -> None:
    """Entity extraction uses patterns, not hard-coded names."""
    text = "Trần Minh Đức quản lý dự án ABC tại Phòng KHCN"
    entities = extract_entities_from_text(text)
    # Should find "Trần Minh Đức" and "KHCN" via patterns.
    assert any("Trần Minh Đức" in e for e in entities)
    assert any("KHCN" in e for e in entities)


def test_table_aware_full_pipeline_produces_all_chunk_types() -> None:
    """Full pipeline produces table_row, entity_summary, and text chunks."""
    text = (
        "Đây là nội dung mở đầu trước bảng.\n\n"
        + VIETNAMESE_TABLE
        + "\nĐây là nội dung sau bảng."
    )
    all_chunks, _ = table_aware_chunk_text(text, chunk_size=500)

    chunk_types = {c.get("metadata", {}).get("chunk_type") for c in all_chunks}
    assert "table_row" in chunk_types
    assert "entity_summary" in chunk_types
    assert "text" in chunk_types


def test_row_chunk_keeps_full_row_together() -> None:
    """A row chunk must contain ALL cell values from the same row. No splitting."""
    tables = detect_tables_in_text(VIETNAMESE_TABLE)
    chunks = table_to_row_chunks(tables[0])

    # Row 1: Xây dựng hệ thống | Phòng CNTT | Nguyễn Quang Lâm
    row1 = chunks[0]["content"]
    assert "Xây dựng hệ thống" in row1
    assert "CNTT" in row1
    assert "Nguyễn Quang Lâm" in row1
