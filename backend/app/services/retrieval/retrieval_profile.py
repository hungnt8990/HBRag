"""Retrieval profile — cấu hình DOMAIN cho pipeline retrieval (config-driven).

Tách mọi hằng số gắn với MỘT nguồn dữ liệu/bài toán (tên field + boost BM25, lexicon
đơn vị/loại văn bản, trọng số chunk_type, giới hạn context/passage) ra 1 dataclass —
pipeline fusion/BM25 đọc từ profile thay vì hard-code. Bài toán mới = thêm 1 instance
vào ``_PROFILES`` (KHÔNG cần sửa lõi pipeline), chọn qua setting
``document_search_retrieval_profile`` (mặc định ``kho_ai``).

Lưu ý phạm vi: 1 process chạy 1 profile (module retrieval nạp profile lúc import).
Trọng số RRF/CRAG/rerank KHÔNG nằm ở đây — đã config-driven qua settings
``document_search_fusion_*`` / ``_crag_*`` / ``_rerank_*``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Mapping


@dataclass(frozen=True)
class RetrievalProfile:
    """Cấu hình domain cho retrieval. Mọi field có default = profile kho AI ngành điện."""

    name: str

    # ----- Index/collection (None = đọc từ settings tương ứng lúc dùng) -----
    es_doc_index: str | None = None    # mặc định settings.doffice_documents_index_name
    es_chunk_index: str | None = None  # mặc định settings.doffice_chunks_index_name

    # ----- Doc-level BM25 (index nguồn, schema BA) -----
    doc_text_fields: tuple[tuple[str, float], ...] = (
        ("document_no", 6.0),
        ("title", 3.0),
        ("summary", 2.0),
        ("issuer_org_name", 1.5),
        ("signer", 1.0),
        ("ocr_content", 1.2),
    )
    # content_fields cho build_query_body (bm25/hybrid — đã tách org token).
    doc_content_fields: tuple[str, ...] = (
        "title^4", "summary^2.5", "signer^1.5", "ocr_content^1.0", "issuer_org_name^0.5",
    )
    doc_phrase_fields: tuple[str, ...] = ("title^6", "summary^3", "ocr_content^0.8")
    doc_phrase_prefix_fields: tuple[str, ...] = ("title^3", "summary^2", "ocr_content^0.4")
    doc_source_fields: tuple[str, ...] = (
        "id", "document_id", "document_no", "title",
        "summary", "issuer_org_name", "signer", "issue_date", "issue_year",
    )
    doc_highlight_fields: tuple[str, ...] = ("ocr_content", "title")
    doc_identifier_field: str = "document_no"   # số/ký hiệu văn bản (match/term)
    doc_key_field: str = "document_id"          # khoá gom kết quả theo văn bản
    year_field: str = "issue_year"              # filter năm (integer, index nguồn)
    month_field: str = "issue_month"            # filter tháng (integer, index nguồn)
    recency_date_field: str = "issue_date"      # gauss decay ưu tiên văn bản mới (date)

    # ----- Chunk-level BM25 (ES chunk index) -----
    chunk_text_fields: tuple[tuple[str, float], ...] = (
        ("chunk_text", 1.0),
        ("section_path", 1.5),
        ("title", 2.0),
        ("table_context", 1.2),
    )
    chunk_phrase_fields: tuple[str, ...] = ("chunk_text^2", "section_path^3", "title^3")
    chunk_source_fields: tuple[str, ...] = (
        "document_id", "id", "id_full", "chunk_id", "chunk_order", "chunk_type",
        "chunk_text", "section_path", "title", "issue_date", "content_hash",
    )
    # function_score theo chunk_type: nhân điểm điều khoản/mục, hạ footer/mục lục.
    chunk_type_weights: tuple[tuple[str, float], ...] = (
        ("legal_clause", 1.8),
        ("document_section", 1.4),
        ("footer_signature", 0.35),
        ("table_of_contents", 0.25),
    )
    chunk_date_field: str = "issue_date"        # filter năm/tháng qua range (date, chunk index)
    doc_link_field: str = "id_full"             # khoá gom chunk theo văn bản nguồn
    chunk_order_field: str = "chunk_order"      # thứ tự chunk trong văn bản

    # ----- Context expansion (hàng xóm ±1 + chunk cha) -----
    parent_chunk_types: frozenset[str] = frozenset(
        {"legal_clause", "document_section", "document_header"}
    )
    parent_lookback_chunks: int = 40
    max_context_items: int = 8
    max_context_chars_per_chunk: int = 1800
    # Ngân sách TỔNG ký tự context/candidate — chunk nhỏ không "ăn" slot ngang chunk lớn
    # (bổ trợ cho max_context_items; cái nào chạm trước thì dừng trước).
    max_context_total_chars: int = 12000

    # ----- Chat passage -----
    max_passage_chars: int = 2000

    # ----- Lexicon domain (đơn vị/loại văn bản — ngành điện EVNCPC) -----
    # Mã đơn vị xuất hiện trong document_no -> ưu tiên văn bản CỦA đơn vị được nhắc.
    org_codes: frozenset[str] = frozenset({
        "evncpc", "evn", "evnict", "cpcit", "cpc", "cpccc", "cdmt", "evnspc", "evnnpc",
        "dnpc", "khpc", "glpc", "qnpc", "qbpc", "qtpc", "ttpc", "pypc", "knpc", "dlpc",
        "bdpc", "klpc",
    })
    org_alias: Mapping[str, str] = field(default_factory=lambda: {"cpcit": "it"})
    org_issuer_query: Mapping[str, str] = field(default_factory=lambda: {
        "cpcit": "cong ty cntt dien luc mien trung",
        "evncpc": "tong cong ty dien luc mien trung",
        "cpc": "tong cong ty dien luc mien trung",
    })
    # Mã THỂ THỨC văn bản (đứng trước SỐ khi tra cứu ký hiệu, vd "qd 258").
    doc_type_abbr: frozenset[str] = frozenset({
        "qd", "tb", "kh", "ct", "nq", "bc", "ttr", "hd", "qc", "cv", "gm", "tl", "tt",
        "cd", "nd",
    })


# Profile mặc định: kho AI dùng chung — văn bản hành chính ngành điện (EVNCPC).
KHO_AI_PROFILE = RetrievalProfile(name="kho_ai")

_PROFILES: dict[str, RetrievalProfile] = {
    KHO_AI_PROFILE.name: KHO_AI_PROFILE,
}


def register_retrieval_profile(profile: RetrievalProfile) -> None:
    """Đăng ký profile cho bài toán mới (gọi lúc import/startup, trước khi retrieval chạy)."""
    _PROFILES[profile.name] = profile
    get_retrieval_profile.cache_clear()


@lru_cache
def get_retrieval_profile(name: str | None = None) -> RetrievalProfile:
    """Profile theo tên; None = đọc setting ``document_search_retrieval_profile``.

    Tên lạ -> cảnh báo + dùng ``kho_ai`` (không vỡ request)."""
    if name is None:
        from app.core.config import settings

        name = str(getattr(settings, "document_search_retrieval_profile", "") or "kho_ai")
    profile = _PROFILES.get(name)
    if profile is None:
        import logging

        logging.getLogger(__name__).warning(
            "Retrieval profile %r không tồn tại — dùng mặc định kho_ai.", name
        )
        return KHO_AI_PROFILE
    return profile
