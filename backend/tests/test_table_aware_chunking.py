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
    table_to_row_chunks,
)

VIETNAMESE_TABLE = (
    "STT | Ná»™i dung | ÄÆ¡n vá»‹ | NgÆ°á»i thá»±c hiá»‡n\n"
    "1 | XÃ¢y dá»±ng há»‡ thá»‘ng | PhÃ²ng CNTT | Nguyá»…n Quang LÃ¢m\n"
    "2 | Quáº£n lÃ½ dá»¯ liá»‡u | PhÃ²ng CNTT | Nguyá»…n Quang LÃ¢m\n"
    "3 | ÄÃ o táº¡o nhÃ¢n sá»± | PhÃ²ng NhÃ¢n sá»± | Tráº§n VÄƒn An\n"
    "4 | Báº£o trÃ¬ háº¡ táº§ng | PhÃ²ng CNTT | Nguyá»…n Quang LÃ¢m\n"
    "5 | Kiá»ƒm tra cháº¥t lÆ°á»£ng | PhÃ²ng QA | Nguyá»…n Quang LÃ¢m\n"
)

ENGLISH_TABLE = (
    "No | Project | Owner | Members | Note\n"
    "1 | Cloud Migration | Team Alpha | John Smith | Q1\n"
    "2 | Data Platform | Team Beta | Jane Doe | Q2\n"
    "3 | Security Audit | Team Alpha | John Smith | Q3\n"
    "4 | ML Pipeline | Team Gamma | Alice Wong | Q4\n"
)

PDF_STAFF_AREA_LAYOUT = """
NHIá»†M Vá»¤ CÃC Máº¢NG CÃ”NG NGHá»† Ná»€N Táº¢NG AI
1. Háº¡ táº§ng tÃ­nh toÃ¡n vÃ  triá»ƒn khai
Má»¥c tiÃªu: Báº£o Ä‘áº£m nÄƒng lá»±c tÃ­nh toÃ¡n dÃ¹ng chung.

DANH SÃCH NHÃ‚N Sá»° PHá»¤ TRÃCH Tá»ªNG Máº¢NG CÃ”NG NGHá»† LÃ•I
(Äá»€ XUáº¤T)
PhÃ²ng
STT Máº£ng cÃ´ng nghá»‡ NhÃ¢n sá»± Ä‘á» xuáº¥t
chá»§ trÃ¬
1. Tráº§n Huy
2. Phan Anh Tuáº¥n
1 Háº¡ táº§ng tÃ­nh toÃ¡n vÃ  triá»ƒn khai KTMVT
3. Nguyá»…n VÅ© ThÃ nh
1. Nguyá»…n Thá»‹ TÃ¹ng
XÃ¢y dá»±ng nÄƒng lá»±c mÃ´ hÃ¬nh
2 PTUD 2. Nguyá»…n Huá»³nh ÄÄƒng Khoa
ngÃ´n ngá»¯ ná»™i bá»™
3. VÃµ VÄƒn PhÃºc
1. Tá»‘ng PhÆ°á»›c LÃ¢m
2. Nguyá»…n Quang LÃ¢m
XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯
3 PTUD 3. Nguyá»…n Trá»ng HÃ¹ng
liá»‡u ná»™i bá»™
4. VÃµ VÄƒn HÃ²a
5. ÄoÃ n Gia Hy (kiá»ƒm thá»­)
1. TrÃ¬nh Thanh Tá»‹nh
XÃ¢y dá»±ng dá»‹ch vá»¥ OCR dÃ¹ng
4 PM 2. DÆ°Æ¡ng Sinh Sinh
chung
3. Nguyá»…n Quang LÃ¢m
1. ÄoÃ n Gia Hy
2. VÃµ VÄƒn PhÃºc
3. VÃµ VÄƒn HÃ²a
4. Nguyá»…n Ngá»c Thá»‹nh
5 Kho dá»¯ liá»‡u AI dÃ¹ng chung VH
5. Nguyá»…n Trá»ng HÃ¹ng
6. Nguyá»…n Huá»³nh ÄÄƒng Khoa
7. Nguyá»…n Thá»‹ TÃ¹ng
8. Nguyá»…n Quang LÃ¢m
CÃ¡c nhÃ¢n sá»± trong káº¿ hoáº¡ch
PoC ThinkLabs:
1. Phan Anh Tuáº¥n
2. Tráº§n Huy
3. Nguyá»…n VÅ© ThÃ nh
4. Nguyá»…n Thá»‹ TÃ¹ng
5. Tá»‘ng PhÆ°á»›c LÃ¢m
6 PTUD 6. Nguyá»…n Huá»³nh ÄÄƒng Khoa
Platform AI
7. Nguyá»…n Quang LÃ¢m
8. Nguyá»…n Trá»ng HÃ¹ng
9. VÃµ VÄƒn PhÃºc
10. VÃµ VÄƒn HÃ²a
11. ÄoÃ n Gia Hy
12. Nguyá»…n Há»¯u Thiá»‡n Äá»©c
13. Trá»‹nh Tháº¿ Phong
á»¨ng dá»¥ng AI vÃ o cÃ¡c pháº§n má»m PhÃ²ng P.PM vÃ  cÃ¡c nhÃ¢n sá»±
7 PM
nghiá»‡p vá»¥ EVN/EVNCPC khÃ¡c do P.PM Ä‘á» xuáº¥t
PhÃ²ng
STT Máº£ng cÃ´ng nghá»‡ NhÃ¢n sá»± Ä‘á» xuáº¥t
chá»§ trÃ¬
Triá»ƒn khai vÃ  tá»± Ä‘á»™ng hÃ³a nghiá»‡p PhÃ²ng P.VH vÃ  cÃ¡c nhÃ¢n sá»±
8 VH
vá»¥ khÃ¡c do P.VH Ä‘á» xuáº¥t
1. Nguyá»…n Há»¯u Thiá»‡n Äá»©c
9 An toÃ n thÃ´ng tin cho AI ATTT 2. Trá»‹nh Tháº¿ Phong
3. ÄoÃ n Gia Hy
Äiá»ƒm cáº§n lÆ°u Ã½ & Ä‘á» xuáº¥t
- CÃ¡c phÃ²ng xem xÃ©t vÃ  bá»‘ trÃ­ nhÃ¢n sá»± phÃ¹ há»£p.
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

    # Nguyá»…n Quang LÃ¢m appears in 4 rows â†’ should get a summary.
    lam_summaries = [
        s for s in summaries
        if s["metadata"].get("entity_name") == "Nguyá»…n Quang LÃ¢m"
    ]
    assert len(lam_summaries) >= 1
    summary = lam_summaries[0]
    assert summary["metadata"]["chunk_type"] == "entity_summary"
    assert summary["metadata"]["entity_name"] == "Nguyá»…n Quang LÃ¢m"
    assert summary["metadata"]["row_count"] >= 4


def test_entity_retrieval_finds_all_related_rows() -> None:
    """Test 4: Query for entity retrieves all rows containing that entity."""
    all_chunks, entity_index = table_aware_chunk_text(VIETNAMESE_TABLE)

    # Simulate entity lookup.
    assert "Nguyá»…n Quang LÃ¢m" in entity_index.entities
    related_indices = entity_index.entities["Nguyá»…n Quang LÃ¢m"]
    assert len(related_indices) >= 4

    # The entity_summary chunk must exist.
    summaries = [
        c for c in all_chunks
        if c.get("metadata", {}).get("entity_name") == "Nguyá»…n Quang LÃ¢m"
    ]
    assert summaries
    # Summary contains all related tasks.
    summary_text = summaries[0]["content"]
    assert "XÃ¢y dá»±ng há»‡ thá»‘ng" in summary_text
    assert "Quáº£n lÃ½ dá»¯ liá»‡u" in summary_text
    assert "Báº£o trÃ¬ háº¡ táº§ng" in summary_text
    assert "Kiá»ƒm tra cháº¥t lÆ°á»£ng" in summary_text


def test_generic_entity_extraction_no_hardcoding() -> None:
    """Entity extraction uses patterns, not hard-coded names."""
    text = "Tráº§n Minh Äá»©c quáº£n lÃ½ dá»± Ã¡n ABC táº¡i PhÃ²ng KHCN"
    entities = extract_entities_from_text(text)
    # Should find "Tráº§n Minh Äá»©c" and "KHCN" via patterns.
    assert any("Tráº§n Minh Äá»©c" in e for e in entities)
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
        "ÄÃ¢y lÃ  ná»™i dung má»Ÿ Ä‘áº§u trÆ°á»›c báº£ng.\n\n"
        + VIETNAMESE_TABLE
        + "\nÄÃ¢y lÃ  ná»™i dung sau báº£ng."
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

    # Row 1: XÃ¢y dá»±ng há»‡ thá»‘ng | PhÃ²ng CNTT | Nguyá»…n Quang LÃ¢m
    row1 = chunks[0]["content"]
    assert "XÃ¢y dá»±ng há»‡ thá»‘ng" in row1
    assert "CNTT" in row1
    assert "Nguyá»…n Quang LÃ¢m" in row1

def test_table_aware_keeps_header_and_row_metadata() -> None:
    chunks, _ = table_aware_chunk_text(VIETNAMESE_TABLE, chunk_size=500)
    row_chunk = next(
        chunk for chunk in chunks if chunk.get("metadata", {}).get("chunk_type") == "table_row"
    )

    assert row_chunk["metadata"]["headers"]
    assert row_chunk["metadata"]["row_index"] >= 1
    assert row_chunk["metadata"]["table_id"]

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
    assert row_5["metadata"]["area"] == "Kho dá»¯ liá»‡u AI dÃ¹ng chung"
    assert row_5["metadata"]["area_normalized"] == "kho du lieu ai dung chung"
    assert row_5["metadata"]["lead_department"] == "VH"
    assert row_5["metadata"]["lead_department_normalized"] == "vh"
    assert row_5["metadata"]["staff_names"] == [
        "ÄoÃ n Gia Hy",
        "VÃµ VÄƒn PhÃºc",
        "VÃµ VÄƒn HÃ²a",
        "Nguyá»…n Ngá»c Thá»‹nh",
        "Nguyá»…n Trá»ng HÃ¹ng",
        "Nguyá»…n Huá»³nh ÄÄƒng Khoa",
        "Nguyá»…n Thá»‹ TÃ¹ng",
        "Nguyá»…n Quang LÃ¢m",
    ]
    assert "CÃ¡c nhÃ¢n sá»± trong káº¿ hoáº¡ch PoC ThinkLabs" not in row_5["content"]
    assert "CÃ¡c nhÃ¢n sá»± trong káº¿ hoáº¡ch PoC ThinkLabs" not in row_5["metadata"]["raw_text_clean"]
    assert row_5["metadata"]["chunk_overlap"] == 0
    assert row_5["metadata"]["overlap_applied"] is False
    assert row_5["metadata"]["parse_warning"] == "raw_text_contains_next_row_fragment"

    platform = next(chunk for chunk in row_chunks if chunk["metadata"]["stt"] == "6")
    assert platform["metadata"]["area"] == "Platform AI"
    assert platform["metadata"]["lead_department"] == "PTUD"
    assert platform["metadata"]["staff_names"] == [
        "Phan Anh Tuáº¥n",
        "Tráº§n Huy",
        "Nguyá»…n VÅ© ThÃ nh",
        "Nguyá»…n Thá»‹ TÃ¹ng",
        "Tá»‘ng PhÆ°á»›c LÃ¢m",
        "Nguyá»…n Huá»³nh ÄÄƒng Khoa",
        "Nguyá»…n Quang LÃ¢m",
        "Nguyá»…n Trá»ng HÃ¹ng",
        "VÃµ VÄƒn PhÃºc",
        "VÃµ VÄƒn HÃ²a",
        "ÄoÃ n Gia Hy",
        "Nguyá»…n Há»¯u Thiá»‡n Äá»©c",
        "Trá»‹nh Tháº¿ Phong",
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
    assert "PhÃ²ng P.PM" not in profile_names
    assert "PhÃ²ng P.VH" not in profile_names
    assert "CÃ¡c nhÃ¢n sá»± khÃ¡c do P.PM Ä‘á» xuáº¥t" not in profile_names

    lam_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "Nguyá»…n Quang LÃ¢m"
    )
    assert lam_profile["metadata"]["person_name_normalized"] == "nguyen quang lam"
    lam_areas = {area["stt"]: area["area"] for area in lam_profile["metadata"]["areas"]}
    assert lam_areas == {
        "3": "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™",
        "4": "XÃ¢y dá»±ng dá»‹ch vá»¥ OCR dÃ¹ng chung",
        "5": "Kho dá»¯ liá»‡u AI dÃ¹ng chung",
        "6": "Platform AI",
    }
    assert lam_profile["metadata"]["areas"][0]["area_normalized"] == (
        "xay dung nen tang rag tren du lieu noi bo"
    )
    assert lam_profile["metadata"]["areas"][0]["lead_department_normalized"] == "ptud"
    assert lam_profile["metadata"]["areas"][0]["source_row_id"] == "staff_area_layout_row_3"
    assert "Nguyá»…n Quang LÃ¢m Ä‘Æ°á»£c Ä‘á» xuáº¥t tham gia 04 máº£ng cÃ´ng nghá»‡" in lam_profile[
        "metadata"
    ]["answer_text"]

    hung_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "Nguyá»…n Trá»ng HÃ¹ng"
    )
    hung_areas = [
        (area["area"], area["lead_department"])
        for area in hung_profile["metadata"]["areas"]
    ]
    assert hung_areas == [
        ("XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™", "PTUD"),
        ("Kho dá»¯ liá»‡u AI dÃ¹ng chung", "VH"),
        ("Platform AI", "PTUD"),
    ]

    hy_profile = next(
        chunk
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_profile"
        and chunk.get("metadata", {}).get("person_name") == "ÄoÃ n Gia Hy"
    )
    hy_areas = [
        (area["area"], area["lead_department"])
        for area in hy_profile["metadata"]["areas"]
    ]
    assert hy_areas == [
        ("XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™", "PTUD"),
        ("Kho dá»¯ liá»‡u AI dÃ¹ng chung", "VH"),
        ("Platform AI", "PTUD"),
        ("An toÃ n thÃ´ng tin cho AI", "ATTT"),
    ]
    assert hy_profile["metadata"]["areas"][0]["role_note"] == "kiá»ƒm thá»­"

    summary_names = {
        chunk["metadata"].get("entity_name")
        for chunk in chunks
        if chunk.get("metadata", {}).get("chunk_type") == "entity_summary"
    }
    assert "AI" not in summary_names
    assert "PM NhÃ¢n" not in summary_names
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
    assert overview["metadata"]["section_title"] == "NHIá»†M Vá»¤ CÃC Máº¢NG CÃ”NG NGHá»† Ná»€N Táº¢NG AI"
    assert [chunk["metadata"]["section_id"] for chunk in sections] == ["1"]
    assert sections[0]["metadata"]["section_title"] == "Háº¡ táº§ng tÃ­nh toÃ¡n vÃ  triá»ƒn khai"
    assert "DANH SÃCH NHÃ‚N Sá»°" not in "\n".join(chunk["content"] for chunk in chunks)
    assert "Äiá»ƒm cáº§n lÆ°u Ã½" in note["content"]
