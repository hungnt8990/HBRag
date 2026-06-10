from app.services.rag_answer_service import build_system_prompt
from app.services.table_relationships import (
    analyze_person_area_membership_query,
    parse_technology_area_rows_from_text,
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
        metadata={"chunk_type": "table_row"},
    )

    assert matching_score > unrelated_score
    assert matching_score >= 10


def test_rag_prompt_membership_answer_no_role_hallucination() -> None:
    prompt = build_system_prompt(answer_mode="hybrid", answer_style="table_qa")

    assert "Có or Không" in prompt
    assert "được đề xuất tham gia" in prompt
    assert "do not infer" in prompt
    assert "table_row or entity_profile" in prompt
