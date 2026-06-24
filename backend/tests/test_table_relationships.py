from app.services.rag_answer_service import build_system_prompt
from app.services.table_relationships import (
    analyze_person_area_membership_query,
    build_entity_profile_chunks,
    is_valid_staff_name,
    parse_technology_area_rows_from_table,
    parse_technology_area_rows_from_text,
    row_to_chunk,
    score_person_area_membership_match,
)

STAFF_TABLE_TEXT = """
DANH SÁCH NHÂN SỰ PHỤ TRÁCH TỪNG MẢNG CÔNG NGHỆ LÕI
STT Mảng công nghệ Phòng chủ trì Nhân sự đề xuất
3 Xây dựng nền tảng RAG trên dữ liệu nội bộ PTUD 1. Tống Phước Lâm
2. Nguyễn Quang Lâm
3. Nguyễn Trọng Hùng
4. Võ Văn Hòa
5. Đoàn Gia Hy (kiểm thử)
4 Xây dựng dịch vụ OCR dùng chung PM 1. Trịnh Thanh Tịnh
2. Dương Sinh Sinh
3. Nguyễn Quang Lâm
"""


def test_parse_staff_table_rows_from_pdf_text() -> None:
    rows = parse_technology_area_rows_from_text(STAFF_TABLE_TEXT, page_number=5)

    row3 = next(row for row in rows if row.stt == "3")
    row4 = next(row for row in rows if row.stt == "4")

    assert row3.area == "Xây dựng nền tảng RAG trên dữ liệu nội bộ"
    assert "Nguyễn Trọng Hùng" in row3.staff_names
    assert "Trịnh Thanh Tịnh" not in row3.staff_names
    assert "Nguyễn Trọng Hùng" not in row4.staff_names
    assert row3.proposed_staff[-1].role_note == "kiểm thử"


def test_reject_broken_pdf_staff_row_text() -> None:
    broken_text = (
        "6 PTUD 6. Nguyễn Huỳnh Đăng Khoa Platform AI 7. Nguyễn Quang Lâm "
        "8. Nguyễn Trọng Hùng 9. Võ Văn Phúc 10. Võ Văn Hòa 11. Đoàn Gia Hy "
        "12. Nguyễn Hữu Thiện Đức 13. Trịnh Thế Phong Ứng dụng AI vào các "
        "phần mềm Phòng P."
    )

    rows = parse_technology_area_rows_from_text(broken_text, source_kind="pdf")

    assert not any(row.stt == "6" for row in rows)
    assert not any("và các nhân sự" in row.staff_names for row in rows)


def test_reject_fake_staff_names() -> None:
    invalid_names = [
        "và các nhân sự",
        "đề xuất",
        "thực hiện đánh giá hiệu quả công việc của từng cá nhân theo định kỳ...",
        "Nguyễn Thị Tùng Xây dựng năng lực mô hình",
    ]

    assert all(not is_valid_staff_name(name) for name in invalid_names)


def test_valid_staff_area_rows_from_structured_table() -> None:
    rows = parse_technology_area_rows_from_table(
        [
            ["STT", "Mảng công nghệ", "Phòng chủ trì", "Nhân sự đề xuất"],
            [
                "3",
                "Xây dựng nền tảng RAG trên dữ liệu nội bộ",
                "PTUD",
                "1. Tống Phước Lâm 2. Nguyễn Quang Lâm 3. Nguyễn Trọng Hùng "
                "4. Võ Văn Hòa 5. Đoàn Gia Hy (kiểm thử)",
            ],
        ],
        page_number=5,
        table_id="pdf_p5_1",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.area == "Xây dựng nền tảng RAG trên dữ liệu nội bộ"
    assert "Nguyễn Trọng Hùng" in row.staff_names
    assert row.proposed_staff[-1].name == "Đoàn Gia Hy"
    assert row.proposed_staff[-1].role_note == "kiểm thử"
    assert row.confidence >= 0.9


def test_entity_profiles_only_from_valid_high_confidence_rows() -> None:
    valid_row = parse_technology_area_rows_from_table(
        [
            ["STT", "Mảng công nghệ", "Phòng chủ trì", "Nhân sự đề xuất"],
            [
                "3",
                "Xây dựng nền tảng RAG trên dữ liệu nội bộ",
                "PTUD",
                "1. Nguyễn Trọng Hùng",
            ],
        ],
    )[0]
    invalid_chunk = {
        "chunk_index": 1,
        "content": "STT: 9\nNhân sự đề xuất: và các nhân sự",
        "metadata": {
            "chunk_type": "table_row",
            "relationship_type": "technology_area_staff",
            "confidence": 0.95,
            "stt": "9",
            "area": "Platform AI 1. Nguyễn Văn A 2. Trần Văn B 3. Phan Văn C",
            "lead_department": "PTUD",
            "staff_names": ["và các nhân sự", "đề xuất"],
            "staff": [
                {"name": "và các nhân sự", "role_note": None},
                {"name": "đề xuất", "role_note": None},
            ],
        },
    }

    profiles = build_entity_profile_chunks([row_to_chunk(valid_row), invalid_chunk])

    assert [profile["metadata"]["person_name"] for profile in profiles] == [
        "Nguyễn Trọng Hùng"
    ]


def test_person_area_membership_retrieval_boost() -> None:
    query = (
        "Nguyễn Trọng Hùng tham gia Xây dựng nền tảng RAG trên dữ liệu nội bộ "
        "đúng không"
    )
    analysis = analyze_person_area_membership_query(query)
    assert analysis is not None
    assert analysis.person_candidate == "Nguyễn Trọng Hùng"

    matching_score = score_person_area_membership_match(
        analysis,
        content="Nhân sự: Nguyễn Trọng Hùng. Xây dựng nền tảng RAG trên dữ liệu nội bộ.",
        metadata={
            "chunk_type": "entity_profile",
            "relationship_type": "technology_area_staff",
            "confidence": 0.95,
            "person_name": "Nguyễn Trọng Hùng",
            "areas": [
                {
                    "area": "Xây dựng nền tảng RAG trên dữ liệu nội bộ",
                    "lead_department": "PTUD",
                }
            ],
        },
    )
    unrelated_score = score_person_area_membership_match(
        analysis,
        content="Nhân sự: Trịnh Thanh Tịnh. Xây dựng dịch vụ OCR dùng chung.",
        metadata={"chunk_type": "table_row", "table_parse_warning": True},
    )

    assert matching_score > unrelated_score
    assert matching_score >= 10


def test_rag_prompt_membership_answer_no_role_hallucination() -> None:
    prompt = build_system_prompt(answer_mode="hybrid", answer_style="table_qa")

    assert "clear affirmative or negative in the user's language" in prompt
    assert "proposed/assigned/listed role" in prompt
    assert "do not infer" in prompt
    assert "table_row or entity_profile" in prompt
    assert "business/technical tables named inside the prose" in prompt
    assert "Do not count Markdown tables" in prompt
