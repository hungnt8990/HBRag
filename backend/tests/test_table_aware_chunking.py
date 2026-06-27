"""Tests for generic table-aware chunking.

No column names, table names, person names, or domain logic is hard-coded
in the chunking implementation. These tests verify the system works with
arbitrary table schemas.
"""

from app.services.chunkers.chunker_table_aware_chunking import (
    build_entity_index,
    detect_tables_in_text,
    extract_entities_from_text,
    generate_entity_summary_chunks,
    table_aware_chunk_text,
    table_to_column_chunks,
    table_to_group_chunks,
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

PDF_STAFF_AREA_LAYOUT = """
NHIỆM VỤ CÁC MẢNG CÔNG NGHỆ NỀN TẢNG AI
1. Hạ tầng tính toán và triển khai
Mục tiêu: Bảo đảm năng lực tính toán dùng chung.

DANH SÁCH NHÂN SỰ PHỤ TRÁCH TỪNG MẢNG CÔNG NGHỆ LÕI
(ĐỀ XUẤT)
Phòng
STT Mảng công nghệ Nhân sự đề xuất
chủ trì
1. Trần Huy
2. Phan Anh Tuấn
1 Hạ tầng tính toán và triển khai KTMVT
3. Nguyễn Vũ Thành
1. Nguyễn Thị Tùng
Xây dựng năng lực mô hình
2 PTUD 2. Nguyễn Huỳnh Đăng Khoa
ngôn ngữ nội bộ
3. Võ Văn Phúc
1. Tống Phước Lâm
2. Nguyễn Quang Lâm
Xây dựng nền tảng RAG trên dữ
3 PTUD 3. Nguyễn Trọng Hùng
liệu nội bộ
4. Võ Văn Hòa
5. Đoàn Gia Hy (kiểm thử)
1. Trình Thanh Tịnh
Xây dựng dịch vụ OCR dùng
4 PM 2. Dương Sinh Sinh
chung
3. Nguyễn Quang Lâm
1. Đoàn Gia Hy
2. Võ Văn Phúc
3. Võ Văn Hòa
4. Nguyễn Ngọc Thịnh
5 Kho dữ liệu AI dùng chung VH
5. Nguyễn Trọng Hùng
6. Nguyễn Huỳnh Đăng Khoa
7. Nguyễn Thị Tùng
8. Nguyễn Quang Lâm
Các nhân sự trong kế hoạch
PoC ThinkLabs:
1. Phan Anh Tuấn
2. Trần Huy
3. Nguyễn Vũ Thành
4. Nguyễn Thị Tùng
5. Tống Phước Lâm
6 PTUD 6. Nguyễn Huỳnh Đăng Khoa
Platform AI
7. Nguyễn Quang Lâm
8. Nguyễn Trọng Hùng
9. Võ Văn Phúc
10. Võ Văn Hòa
11. Đoàn Gia Hy
12. Nguyễn Hữu Thiện Đức
13. Trịnh Thế Phong
Ứng dụng AI vào các phần mềm Phòng P.PM và các nhân sự
7 PM
nghiệp vụ EVN/EVNCPC khác do P.PM đề xuất
Phòng
STT Mảng công nghệ Nhân sự đề xuất
chủ trì
Triển khai và tự động hóa nghiệp Phòng P.VH và các nhân sự
8 VH
vụ khác do P.VH đề xuất
1. Nguyễn Hữu Thiện Đức
9 An toàn thông tin cho AI ATTT 2. Trịnh Thế Phong
3. Đoàn Gia Hy
Điểm cần lưu ý & đề xuất
- Các phòng xem xét và bố trí nhân sự phù hợp.
"""


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

def test_entity_extraction_stops_at_context_boundary_words() -> None:
    text = (
        "TABLE_ROW row=5 | Area: Shared data platform | Members: "
        "8. Nguyen Quang Lam Cac nhan su trong ke hoach PoC ThinkLabs"
    )

    entities = extract_entities_from_text(text)

    assert "Nguyen Quang Lam" in entities
    assert all(not entity.startswith("Nguyen Quang Lam Cac") for entity in entities)


def test_entity_extraction_ignores_table_markers() -> None:
    text = "TABLE_ROW table_id=pdf_p1_1 row=1 | owner: Nguyen Quang Lam"

    entities = extract_entities_from_text(text)

    assert "TABLE_ROW" not in entities
    assert "Nguyen Quang Lam" in entities


def test_entity_summary_chunks_are_bounded_by_chunk_size() -> None:
    text = "Name | Area\n" + "\n".join(
        f"Nguyen Quang Lam | Very long area description number {index} with supporting text"
        for index in range(20)
    )

    chunks, entity_index = table_aware_chunk_text(text, chunk_size=500)
    summaries = [
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_summary"
    ]

    assert entity_index.entities
    assert len(summaries) > 1
    assert max(len(summary["content"]) for summary in summaries) < 900


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





def test_table_aware_chunk_text_does_not_duplicate_trailing_text() -> None:
    text = (
        "Intro\n\n"
        "Name | Area\n"
        "Nguyen Quang Lam | Platform\n"
        "Tran Van An | QA\n\n"
        "Tail section once."
    )
    chunks, _ = table_aware_chunk_text(text, chunk_size=200)
    text_chunks = [
        c["content"]
        for c in chunks
        if c.get("metadata", {}).get("chunk_type") == "text"
    ]
    assert sum("Tail section once." in chunk for chunk in text_chunks) == 1

def test_row_chunk_keeps_full_row_together() -> None:
    """A row chunk must contain ALL cell values from the same row. No splitting."""
    tables = detect_tables_in_text(VIETNAMESE_TABLE)
    chunks = table_to_row_chunks(tables[0])

    # Row 1: Xây dựng hệ thống | Phòng CNTT | Nguyễn Quang Lâm
    row1 = chunks[0]["content"]
    assert "Xây dựng hệ thống" in row1
    assert "CNTT" in row1
    assert "Nguyễn Quang Lâm" in row1

def test_table_aware_keeps_header_and_row_metadata() -> None:
    chunks, _ = table_aware_chunk_text(VIETNAMESE_TABLE, chunk_size=500)
    row_chunk = next(
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "table_row"
    )

    assert row_chunk["metadata"]["headers"]
    assert row_chunk["metadata"]["row_index"] >= 1
    assert row_chunk["metadata"]["table_id"]


def test_table_aware_emits_group_and_column_chunks() -> None:
    text = (
        "No | System | Owner | Status\n"
        "1 | CRM | Team A | Build\n"
        "2 | ERP | Team B | Review\n"
        "3 | GIS | Team A | Done\n"
    )
    table = detect_tables_in_text(text)[0]

    groups = table_to_group_chunks(table, group_size=2)
    columns = table_to_column_chunks(table, chunk_size=400)

    assert groups
    assert groups[0]["metadata"]["chunk_type"] == "table_group"
    assert groups[0]["metadata"]["row_start"] == 1
    assert groups[0]["metadata"]["row_end"] == 2
    assert "Header: No | System | Owner | Status" in groups[0]["content"]

    owner_column = next(chunk for chunk in columns if chunk["metadata"]["column_name"] == "Owner")
    assert owner_column["metadata"]["chunk_type"] == "table_column"
    assert owner_column["metadata"]["row_context_headers"]
    assert "| 1 |" in owner_column["content"]
    assert "Team A" in owner_column["content"]

def test_pdf_staff_area_layout_rebuilds_rows_and_person_profiles() -> None:
    chunks, _entity_index = table_aware_chunk_text(PDF_STAFF_AREA_LAYOUT, chunk_size=1200)

    row_chunks = [
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "table_row"
    ]
    assert [chunk["metadata"]["stt"] for chunk in row_chunks] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
    ]

    row_5 = next(chunk for chunk in row_chunks if chunk["metadata"]["stt"] == "5")
    assert row_5["metadata"]["area"] == "Kho dữ liệu AI dùng chung"
    assert row_5["metadata"]["area_normalized"] == "kho du lieu ai dung chung"
    assert row_5["metadata"]["lead_department"] == "VH"
    assert row_5["metadata"]["lead_department_normalized"] == "vh"
    assert row_5["metadata"]["staff_names"] == [
        "Đoàn Gia Hy",
        "Võ Văn Phúc",
        "Võ Văn Hòa",
        "Nguyễn Ngọc Thịnh",
        "Nguyễn Trọng Hùng",
        "Nguyễn Huỳnh Đăng Khoa",
        "Nguyễn Thị Tùng",
        "Nguyễn Quang Lâm",
    ]
    assert "Các nhân sự trong kế hoạch PoC ThinkLabs" not in row_5["content"]
    assert "Các nhân sự trong kế hoạch PoC ThinkLabs" not in row_5["metadata"]["raw_text_clean"]
    assert row_5["metadata"]["chunk_overlap"] == 0
    assert row_5["metadata"]["overlap_applied"] is False
    assert row_5["metadata"]["parse_warning"] == "raw_text_contains_next_row_fragment"

    platform = next(chunk for chunk in row_chunks if chunk["metadata"]["stt"] == "6")
    assert platform["metadata"]["area"] == "Platform AI"
    assert platform["metadata"]["lead_department"] == "PTUD"
    assert platform["metadata"]["staff_names"] == [
        "Phan Anh Tuấn",
        "Trần Huy",
        "Nguyễn Vũ Thành",
        "Nguyễn Thị Tùng",
        "Tống Phước Lâm",
        "Nguyễn Huỳnh Đăng Khoa",
        "Nguyễn Quang Lâm",
        "Nguyễn Trọng Hùng",
        "Võ Văn Phúc",
        "Võ Văn Hòa",
        "Đoàn Gia Hy",
        "Nguyễn Hữu Thiện Đức",
        "Trịnh Thế Phong",
    ]

    row_7 = next(chunk for chunk in row_chunks if chunk["metadata"]["stt"] == "7")
    row_8 = next(chunk for chunk in row_chunks if chunk["metadata"]["stt"] == "8")
    for generic_row, department in ((row_7, "P.PM"), (row_8, "P.VH")):
        assert generic_row["metadata"]["assignment_type"] == "generic_department_assignment"
        assert generic_row["metadata"]["has_specific_person"] is False
        assert generic_row["metadata"]["staff_names"] == []
        assert generic_row["metadata"]["staff"] == []
        assert department in generic_row["metadata"]["generic_assignment_text"]

    profile_names = {
        chunk["metadata"].get("person_name")
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
    }
    assert "Phòng P.PM" not in profile_names
    assert "Phòng P.VH" not in profile_names
    assert "Các nhân sự khác do P.PM đề xuất" not in profile_names

    lam_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "Nguyễn Quang Lâm"
    )
    assert lam_profile["metadata"]["person_name_normalized"] == "nguyen quang lam"
    lam_areas = {area["stt"]: area["area"] for area in lam_profile["metadata"]["areas"]}
    assert lam_areas == {
        "3": "Xây dựng nền tảng RAG trên dữ liệu nội bộ",
        "4": "Xây dựng dịch vụ OCR dùng chung",
        "5": "Kho dữ liệu AI dùng chung",
        "6": "Platform AI",
    }
    assert lam_profile["metadata"]["areas"][0]["area_normalized"] == (
        "xay dung nen tang rag tren du lieu noi bo"
    )
    assert lam_profile["metadata"]["areas"][0]["lead_department_normalized"] == "ptud"
    assert lam_profile["metadata"]["areas"][0]["source_row_id"] == "staff_area_layout_row_3"
    assert "Nguyễn Quang Lâm được đề xuất tham gia 04 mảng công nghệ" in lam_profile[
        "metadata"
    ]["answer_text"]

    hung_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "Nguyễn Trọng Hùng"
    )
    hung_areas = [
        (area["area"], area["lead_department"])
        for area in hung_profile["metadata"]["areas"]
    ]
    assert hung_areas == [
        ("Xây dựng nền tảng RAG trên dữ liệu nội bộ", "PTUD"),
        ("Kho dữ liệu AI dùng chung", "VH"),
        ("Platform AI", "PTUD"),
    ]

    hy_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "Đoàn Gia Hy"
    )
    hy_areas = [
        (area["area"], area["lead_department"])
        for area in hy_profile["metadata"]["areas"]
    ]
    assert hy_areas == [
        ("Xây dựng nền tảng RAG trên dữ liệu nội bộ", "PTUD"),
        ("Kho dữ liệu AI dùng chung", "VH"),
        ("Platform AI", "PTUD"),
        ("An toàn thông tin cho AI", "ATTT"),
    ]
    assert hy_profile["metadata"]["areas"][0]["role_note"] == "kiểm thử"

    summary_names = {
        chunk["metadata"].get("entity_name")
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_summary"
    }
    assert "AI" not in summary_names
    assert "PM Nhân" not in summary_names
    assert summary_names <= {"PTUD", "PM", "VH", "ATTT", "KTMVT", *profile_names}

    overview = next(
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "overview"
    )
    sections = [
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "section"
    ]
    note = next(
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "note"
    )
    assert overview["metadata"]["section_title"] == "NHIỆM VỤ CÁC MẢNG CÔNG NGHỆ NỀN TẢNG AI"
    assert [chunk["metadata"]["section_id"] for chunk in sections] == ["1"]
    assert sections[0]["metadata"]["section_title"] == "Hạ tầng tính toán và triển khai"
    assert "DANH SÁCH NHÂN SỰ" not in "\n".join(chunk["content"] for chunk in chunks)
    assert "Điểm cần lưu ý" in note["content"]
