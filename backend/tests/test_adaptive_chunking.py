from __future__ import annotations

from app.services.chunkers.chunker_adaptive_chunking import (
    apply_chunk_quality_gate,
    build_body_evidence_chunks,
    normalize_text_for_chunking,
)
from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source


def test_normalize_text_for_chunking_keeps_vietnamese_and_normalizes_dashes() -> None:
    text = "Quyền lợi\u00a0người lao động – áp dụng\r\n\r\n\r\nĐiều 1. Nội dung"

    normalized = normalize_text_for_chunking(text)

    assert "Quyền lợi người lao động - áp dụng" in normalized
    assert "\r" not in normalized
    assert "\n\n\n" not in normalized


def test_legal_clause_chunk_keeps_article_context() -> None:
    chunks = build_body_evidence_chunks(
        text="""
Chương II. Chế độ
Điều 10. Chế độ nghỉ việc riêng
1. Người lao động được nghỉ 03 ngày khi kết hôn.
a) Điều kiện áp dụng theo quy chế.
Điều 11. Chế độ khác
1. Nội dung khác.
""",
        base_metadata={
            "source_type": "doffice_elasticsearch",
            "document_code": "01/QD-CPC",
            "trich_yeu": "Quy định chế độ",
            "issued_date": "01/06/2026",
            "issuer": "EVNCPC",
        },
    )

    article_10 = next(chunk for chunk in chunks if chunk.metadata["article_number"] == "10")

    assert article_10.metadata["chunk_type"] == "legal_clause"
    assert article_10.metadata["article_title"] == "Chế độ nghỉ việc riêng"
    assert article_10.metadata["clause_number"] == "1"
    assert article_10.metadata["point_label"] == "a"
    assert "Văn bản: 01/QD-CPC - Quy định chế độ" in article_10.content
    assert "Điều 10. Chế độ nghỉ việc riêng" in article_10.content



def test_legal_clause_summary_stays_clause_scoped() -> None:
    chunks = build_body_evidence_chunks(
        text="""
Điều 1. Nội dung đào tạo
1. Thời gian đào tạo từ ngày 17/06/2026 đến ngày 19/06/2026.
2. Địa điểm đào tạo tại Hà Nội.
3. Kinh phí do CPCIT chi trả.
Điều 2. Tổ chức thực hiện
1. Các đơn vị liên quan triển khai thực hiện.
""",
        base_metadata={
            "source_type": "doffice_elasticsearch",
            "document_code": "608/QĐ-IT",
            "trich_yeu": "Quyết định đào tạo",
        },
    )

    article_1 = next(chunk for chunk in chunks if chunk.metadata["article_number"] == "1")

    assert article_1.metadata["summary"].startswith("Điều 1 quy định:")
    assert "Thời gian đào tạo" in article_1.metadata["summary"]
    assert "Địa điểm đào tạo" in article_1.metadata["summary"]
    assert "Tổ chức thực hiện" not in article_1.metadata["summary"]


def test_structure_chunks_keep_hierarchical_heading_path() -> None:
    chunks = build_body_evidence_chunks(
        text="""
PHU LUC 02
1. Objective
1.1. Scope
Scope details.
2. Delivery
Delivery details.
""",
        base_metadata={"document_code": "01/PL", "chunk_type": "document_body"},
    )

    scoped = next(chunk for chunk in chunks if chunk.metadata.get("section_title") == "1.1. Scope")

    assert scoped.metadata["heading_path"] == ["PHU LUC 02", "1. Objective", "1.1. Scope"]
    assert scoped.metadata["section_path"] == ["PHU LUC 02", "1. Objective", "1.1. Scope"]


def test_quality_gate_blocks_table_placeholders_and_bad_table_rows() -> None:
    placeholder = apply_chunk_quality_gate(
        "Nội dung có [[TABLE_1]] chưa được thay thế.",
        {"source_type": "doffice_elasticsearch", "chunk_type": "document_section", "document_code": "01/QD"},
    )
    bad_row = apply_chunk_quality_gate(
        "Dòng 1: thiếu header",
        {"source_type": "doffice_elasticsearch", "chunk_type": "table_row", "document_code": "01/QD"},
    )

    assert placeholder.metadata["indexable"] is False
    assert placeholder.metadata["embedding_enabled"] is False
    assert "table_placeholder" in placeholder.metadata["quality_gate_reasons"]
    assert bad_row.metadata["indexable"] is False
    assert "missing_table_title" in bad_row.metadata["quality_gate_reasons"]
    assert "missing_table_headers" in bad_row.metadata["quality_gate_reasons"]


def test_doffice_table_chunk_keeps_context_and_logical_table_id() -> None:
    source = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT",
        "trich_yeu": "Bảng phân công nhiệm vụ",
        "noi_ban_hanh": "EVNCPC",
        "ngay_vb": "2026-06-04",
        "noi_dung": """
Danh sách phân công nhiệm vụ
<table>
  <tr><th>STT</th><th>Người phụ trách</th><th>Nhiệm vụ</th><th>Thời hạn</th></tr>
  <tr><td>1</td><td>Nguyễn Văn A</td><td>Tổng hợp báo cáo</td><td>30/06/2026</td></tr>
  <tr><td>2</td><td>Trần Thị B</td><td>Rà soát dữ liệu</td><td>25/06/2026</td></tr>
</table>
Nơi nhận:
- Như trên;
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    # Builder v2: bảng -> 1 chunk_type="table" (không nổ thành nhiều table_row).
    table_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table"]

    assert len(table_chunks) == 1
    table = table_chunks[0]
    assert table.metadata["table_title"] == "Danh sách phân công nhiệm vụ"
    assert table.metadata["table_headers"] == ["STT", "Người phụ trách", "Nhiệm vụ", "Thời hạn"]
    assert table.metadata["row_count"] == 2
    # Dữ liệu các dòng vẫn nằm đầy đủ trong nội dung markdown.
    assert "Nguyễn Văn A" in table.content
    assert "Trần Thị B" in table.content
    assert table.metadata["logical_table_id"].startswith("ltbl_")
    assert table.metadata["source_span"]["start"] < table.metadata["source_span"]["end"]
    assert "[[TABLE_" not in table.content
    # Dòng ngữ cảnh giúp chunk tự đủ nghĩa cho RAG.
    assert "Bảng:" in table.content
    assert "Văn bản: 6515/EVNCPC-VTCNTT" in table.content
