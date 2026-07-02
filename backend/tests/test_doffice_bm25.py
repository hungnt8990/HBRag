from __future__ import annotations

from app.services.retrieval.retrieval_doffice_bm25 import (
    DofficeBm25DocumentStore,
    extract_doffice_body_text,
)


def test_extract_doffice_body_text_strips_metadata_preamble() -> None:
    raw = """THÔNG TIN VĂN BẢN DOFFICE
ID_VB: 1
Số/ký hiệu văn bản: 123/QĐ-IT
Ngày văn bản: 01/01/2026
Trích yếu: Test
Nơi ban hành: Công ty CNTT Điện lực miền Trung
Tên file: test.pdf
Đường dẫn: 2026/test.pdf
TỔNG CÔNG TY ĐIỆN LỰC MIỀN TRUNG
CÔNG TY CÔNG NGHỆ THÔNG TIN
Số: 123/QĐ-IT
QUYẾT ĐỊNH
Về việc ban hành quy định trả lương

Điều 1. Ban hành quy định trả lương trong CPCIT.

Nơi nhận:
- Luu VT.
"""
    body = extract_doffice_body_text(raw)
    assert "THÔNG TIN VĂN BẢN DOFFICE" not in body
    assert "Tên file" not in body
    assert "QUYẾT ĐỊNH" in body
    assert "Điều 1. Ban hành quy định trả lương" in body
    assert "Nơi nhận" not in body


def test_doffice_bm25_mapping_has_body_field_with_boilerplate_analyzer() -> None:
    definition = DofficeBm25DocumentStore._index_definition()
    props = definition["mappings"]["properties"]
    analysis = definition["settings"]["analysis"]
    assert props["noi_dung_body"]["analyzer"] == "vi_bm25_body"
    assert props["noi_dung_body"]["search_analyzer"] == "vi_bm25_body_search"
    assert "vi_doffice_boilerplate_stop" in analysis["filter"]
    assert "vi_synonyms" not in props["ky_hieu"].get("search_analyzer", "")
