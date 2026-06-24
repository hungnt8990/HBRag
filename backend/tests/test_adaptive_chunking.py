from __future__ import annotations

from app.services.chunkers.chunker_adaptive_chunking import (
    apply_chunk_quality_gate,
    build_body_evidence_chunks,
    normalize_text_for_chunking,
)
from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source


def test_normalize_text_for_chunking_keeps_vietnamese_and_normalizes_dashes() -> None:
    text = "Quyá»n lá»£i\u00a0ngÆ°á»i lao Ä‘á»™ng â€“ Ã¡p dá»¥ng\r\n\r\n\r\nÄiá»u 1. Ná»™i dung"

    normalized = normalize_text_for_chunking(text)

    assert "Quyá»n lá»£i ngÆ°á»i lao Ä‘á»™ng - Ã¡p dá»¥ng" in normalized
    assert "\r" not in normalized
    assert "\n\n\n" not in normalized


def test_legal_clause_chunk_keeps_article_context() -> None:
    chunks = build_body_evidence_chunks(
        text="""
ChÆ°Æ¡ng II. Cháº¿ Ä‘á»™
Äiá»u 10. Cháº¿ Ä‘á»™ nghá»‰ viá»‡c riÃªng
1. NgÆ°á»i lao Ä‘á»™ng Ä‘Æ°á»£c nghá»‰ 03 ngÃ y khi káº¿t hÃ´n.
a) Äiá»u kiá»‡n Ã¡p dá»¥ng theo quy cháº¿.
Äiá»u 11. Cháº¿ Ä‘á»™ khÃ¡c
1. Ná»™i dung khÃ¡c.
""",
        base_metadata={
            "source_type": "doffice_elasticsearch",
            "document_code": "01/QD-CPC",
            "trich_yeu": "Quy Ä‘á»‹nh cháº¿ Ä‘á»™",
            "issued_date": "01/06/2026",
            "issuer": "EVNCPC",
        },
    )

    article_10 = next(chunk for chunk in chunks if chunk.metadata["article_number"] == "10")

    assert article_10.metadata["chunk_type"] == "legal_clause"
    assert article_10.metadata["article_title"] == "Cháº¿ Ä‘á»™ nghá»‰ viá»‡c riÃªng"
    assert article_10.metadata["clause_number"] == "1"
    assert article_10.metadata["point_label"] == "a"
    assert "VÄƒn báº£n: 01/QD-CPC - Quy Ä‘á»‹nh cháº¿ Ä‘á»™" in article_10.content
    assert "Äiá»u 10. Cháº¿ Ä‘á»™ nghá»‰ viá»‡c riÃªng" in article_10.content



def test_legal_clause_summary_stays_clause_scoped() -> None:
    chunks = build_body_evidence_chunks(
        text="""
Äiá»u 1. Ná»™i dung Ä‘Ã o táº¡o
1. Thá»i gian Ä‘Ã o táº¡o tá»« ngÃ y 17/06/2026 Ä‘áº¿n ngÃ y 19/06/2026.
2. Äá»‹a Ä‘iá»ƒm Ä‘Ã o táº¡o táº¡i HÃ  Ná»™i.
3. Kinh phÃ­ do CPCIT chi tráº£.
Äiá»u 2. Tá»• chá»©c thá»±c hiá»‡n
1. CÃ¡c Ä‘Æ¡n vá»‹ liÃªn quan triá»ƒn khai thá»±c hiá»‡n.
""",
        base_metadata={
            "source_type": "doffice_elasticsearch",
            "document_code": "608/QÄ-IT",
            "trich_yeu": "Quyáº¿t Ä‘á»‹nh Ä‘Ã o táº¡o",
        },
    )

    article_1 = next(chunk for chunk in chunks if chunk.metadata["article_number"] == "1")

    assert article_1.metadata["summary"].startswith("Äiá»u 1 quy Ä‘á»‹nh:")
    assert "Thá»i gian Ä‘Ã o táº¡o" in article_1.metadata["summary"]
    assert "Äá»‹a Ä‘iá»ƒm Ä‘Ã o táº¡o" in article_1.metadata["summary"]
    assert "Tá»• chá»©c thá»±c hiá»‡n" not in article_1.metadata["summary"]
def test_quality_gate_blocks_table_placeholders_and_bad_table_rows() -> None:
    placeholder = apply_chunk_quality_gate(
        "Ná»™i dung cÃ³ [[TABLE_1]] chÆ°a Ä‘Æ°á»£c thay tháº¿.",
        {"source_type": "doffice_elasticsearch", "chunk_type": "document_section", "document_code": "01/QD"},
    )
    bad_row = apply_chunk_quality_gate(
        "DÃ²ng 1: thiáº¿u header",
        {"source_type": "doffice_elasticsearch", "chunk_type": "table_row", "document_code": "01/QD"},
    )

    assert placeholder.metadata["indexable"] is False
    assert placeholder.metadata["embedding_enabled"] is False
    assert "table_placeholder" in placeholder.metadata["quality_gate_reasons"]
    assert bad_row.metadata["indexable"] is False
    assert "missing_table_title" in bad_row.metadata["quality_gate_reasons"]
    assert "missing_table_headers" in bad_row.metadata["quality_gate_reasons"]


def test_doffice_table_rows_keep_context_cells_and_logical_table_id() -> None:
    source = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT",
        "trich_yeu": "Báº£ng phÃ¢n cÃ´ng nhiá»‡m vá»¥",
        "noi_ban_hanh": "EVNCPC",
        "ngay_vb": "2026-06-04",
        "noi_dung": """
Danh sÃ¡ch phÃ¢n cÃ´ng nhiá»‡m vá»¥
<table>
  <tr><th>STT</th><th>NgÆ°á»i phá»¥ trÃ¡ch</th><th>Nhiá»‡m vá»¥</th><th>Thá»i háº¡n</th></tr>
  <tr><td>1</td><td>Nguyá»…n VÄƒn A</td><td>Tá»•ng há»£p bÃ¡o cÃ¡o</td><td>30/06/2026</td></tr>
  <tr><td>2</td><td>Tráº§n Thá»‹ B</td><td>RÃ  soÃ¡t dá»¯ liá»‡u</td><td>25/06/2026</td></tr>
</table>
NÆ¡i nháº­n:
- NhÆ° trÃªn;
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    table_rows = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_row"]

    assert len(table_rows) == 2
    first = table_rows[0]
    assert first.metadata["table_title"] == "Danh sÃ¡ch phÃ¢n cÃ´ng nhiá»‡m vá»¥"
    assert first.metadata["table_headers"] == ["STT", "NgÆ°á»i phá»¥ trÃ¡ch", "Nhiá»‡m vá»¥", "Thá»i háº¡n"]
    assert first.metadata["row_index"] == 1
    assert first.metadata["row_cells"]["NgÆ°á»i phá»¥ trÃ¡ch"] == "Nguyá»…n VÄƒn A"
    assert first.metadata["logical_table_id"].startswith("ltbl_")
    assert first.metadata["source_span"]["start"] < first.metadata["source_span"]["end"]
    assert "[[TABLE_" not in first.content
