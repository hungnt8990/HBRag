from app.services.rag.rag_answer_service import build_system_prompt
from app.services.chunkers.chunker_table_relationships import (
    analyze_person_area_membership_query,
    build_entity_profile_chunks,
    is_valid_staff_name,
    parse_technology_area_rows_from_table,
    parse_technology_area_rows_from_text,
    row_to_chunk,
    score_person_area_membership_match,
)

STAFF_TABLE_TEXT = """
DANH SÃCH NHÃ‚N Sá»° PHá»¤ TRÃCH Tá»ªNG Máº¢NG CÃ”NG NGHá»† LÃ•I
STT Máº£ng cÃ´ng nghá»‡ PhÃ²ng chá»§ trÃ¬ NhÃ¢n sá»± Ä‘á» xuáº¥t
3 XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ PTUD 1. Tá»‘ng PhÆ°á»›c LÃ¢m
2. Nguyá»…n Quang LÃ¢m
3. Nguyá»…n Trá»ng HÃ¹ng
4. VÃµ VÄƒn HÃ²a
5. ÄoÃ n Gia Hy (kiá»ƒm thá»­)
4 XÃ¢y dá»±ng dá»‹ch vá»¥ OCR dÃ¹ng chung PM 1. Trá»‹nh Thanh Tá»‹nh
2. DÆ°Æ¡ng Sinh Sinh
3. Nguyá»…n Quang LÃ¢m
"""


def test_parse_staff_table_rows_from_pdf_text() -> None:
    rows = parse_technology_area_rows_from_text(STAFF_TABLE_TEXT, page_number=5)

    row3 = next(row for row in rows if row.stt == "3")
    row4 = next(row for row in rows if row.stt == "4")

    assert row3.area == "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™"
    assert "Nguyá»…n Trá»ng HÃ¹ng" in row3.staff_names
    assert "Trá»‹nh Thanh Tá»‹nh" not in row3.staff_names
    assert "Nguyá»…n Trá»ng HÃ¹ng" not in row4.staff_names
    assert row3.proposed_staff[-1].role_note == "kiá»ƒm thá»­"


def test_reject_broken_pdf_staff_row_text() -> None:
    broken_text = (
        "6 PTUD 6. Nguyá»…n Huá»³nh ÄÄƒng Khoa Platform AI 7. Nguyá»…n Quang LÃ¢m "
        "8. Nguyá»…n Trá»ng HÃ¹ng 9. VÃµ VÄƒn PhÃºc 10. VÃµ VÄƒn HÃ²a 11. ÄoÃ n Gia Hy "
        "12. Nguyá»…n Há»¯u Thiá»‡n Äá»©c 13. Trá»‹nh Tháº¿ Phong á»¨ng dá»¥ng AI vÃ o cÃ¡c "
        "pháº§n má»m PhÃ²ng P."
    )

    rows = parse_technology_area_rows_from_text(broken_text, source_kind="pdf")

    assert not any(row.stt == "6" for row in rows)
    assert not any("vÃ  cÃ¡c nhÃ¢n sá»±" in row.staff_names for row in rows)


def test_reject_fake_staff_names() -> None:
    invalid_names = [
        "vÃ  cÃ¡c nhÃ¢n sá»±",
        "Ä‘á» xuáº¥t",
        "thá»±c hiá»‡n Ä‘Ã¡nh giÃ¡ hiá»‡u quáº£ cÃ´ng viá»‡c cá»§a tá»«ng cÃ¡ nhÃ¢n theo Ä‘á»‹nh ká»³...",
        "Nguyá»…n Thá»‹ TÃ¹ng XÃ¢y dá»±ng nÄƒng lá»±c mÃ´ hÃ¬nh",
    ]

    assert all(not is_valid_staff_name(name) for name in invalid_names)


def test_valid_staff_area_rows_from_structured_table() -> None:
    rows = parse_technology_area_rows_from_table(
        [
            ["STT", "Máº£ng cÃ´ng nghá»‡", "PhÃ²ng chá»§ trÃ¬", "NhÃ¢n sá»± Ä‘á» xuáº¥t"],
            [
                "3",
                "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™",
                "PTUD",
                "1. Tá»‘ng PhÆ°á»›c LÃ¢m 2. Nguyá»…n Quang LÃ¢m 3. Nguyá»…n Trá»ng HÃ¹ng "
                "4. VÃµ VÄƒn HÃ²a 5. ÄoÃ n Gia Hy (kiá»ƒm thá»­)",
            ],
        ],
        page_number=5,
        table_id="pdf_p5_1",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.area == "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™"
    assert "Nguyá»…n Trá»ng HÃ¹ng" in row.staff_names
    assert row.proposed_staff[-1].name == "ÄoÃ n Gia Hy"
    assert row.proposed_staff[-1].role_note == "kiá»ƒm thá»­"
    assert row.confidence >= 0.9


def test_entity_profiles_only_from_valid_high_confidence_rows() -> None:
    valid_row = parse_technology_area_rows_from_table(
        [
            ["STT", "Máº£ng cÃ´ng nghá»‡", "PhÃ²ng chá»§ trÃ¬", "NhÃ¢n sá»± Ä‘á» xuáº¥t"],
            [
                "3",
                "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™",
                "PTUD",
                "1. Nguyá»…n Trá»ng HÃ¹ng",
            ],
        ],
    )[0]
    invalid_chunk = {
        "chunk_index": 1,
        "content": "STT: 9\nNhÃ¢n sá»± Ä‘á» xuáº¥t: vÃ  cÃ¡c nhÃ¢n sá»±",
        "metadata": {
            "chunk_type": "table_row",
            "relationship_type": "technology_area_staff",
            "confidence": 0.95,
            "stt": "9",
            "area": "Platform AI 1. Nguyá»…n VÄƒn A 2. Tráº§n VÄƒn B 3. Phan VÄƒn C",
            "lead_department": "PTUD",
            "staff_names": ["vÃ  cÃ¡c nhÃ¢n sá»±", "Ä‘á» xuáº¥t"],
            "staff": [
                {"name": "vÃ  cÃ¡c nhÃ¢n sá»±", "role_note": None},
                {"name": "Ä‘á» xuáº¥t", "role_note": None},
            ],
        },
    }

    profiles = build_entity_profile_chunks([row_to_chunk(valid_row), invalid_chunk])

    assert [profile["metadata"]["person_name"] for profile in profiles] == [
        "Nguyá»…n Trá»ng HÃ¹ng"
    ]


def test_person_area_membership_retrieval_boost() -> None:
    query = (
        "Nguyá»…n Trá»ng HÃ¹ng tham gia XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ "
        "Ä‘Ãºng khÃ´ng"
    )
    analysis = analyze_person_area_membership_query(query)
    assert analysis is not None
    assert analysis.person_candidate == "Nguyá»…n Trá»ng HÃ¹ng"

    matching_score = score_person_area_membership_match(
        analysis,
        content="NhÃ¢n sá»±: Nguyá»…n Trá»ng HÃ¹ng. XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™.",
        metadata={
            "chunk_type": "entity_profile",
            "relationship_type": "technology_area_staff",
            "confidence": 0.95,
            "person_name": "Nguyá»…n Trá»ng HÃ¹ng",
            "areas": [
                {
                    "area": "XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™",
                    "lead_department": "PTUD",
                }
            ],
        },
    )
    unrelated_score = score_person_area_membership_match(
        analysis,
        content="NhÃ¢n sá»±: Trá»‹nh Thanh Tá»‹nh. XÃ¢y dá»±ng dá»‹ch vá»¥ OCR dÃ¹ng chung.",
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
