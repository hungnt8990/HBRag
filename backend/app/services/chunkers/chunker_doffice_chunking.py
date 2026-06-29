from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from app.core.config import settings
from app.repositories.documents import ChunkCreate
from app.services.chunkers.chunker_adaptive_chunking import (
    apply_chunk_quality_gate,
    build_body_evidence_chunks,
)
from app.services.chunkers.chunker_text_cleaning import clean_for_chunking
from app.services.ingestion.ingestion_doffice_content_normalizer import (
    NormalizedDofficeDocument,
    NormalizedElement,
    NormalizedTable,
)

# Các element "view" do normalizer sinh ra từ mỗi bảng. Builder v2 BỎ HẲN việc
# tạo chunk từ chúng (tránh table-explosion ~4x/bảng); bảng được xử lý riêng từ
# ``normalized.tables``.
TABLE_VIEW_CHUNK_TYPES = {"table_parent", "table_row", "table_group", "table_column"}

# Ngưỡng ký tự để quyết định 1 bảng có cần cắt nhỏ hay không.
TABLE_CHUNK_MAX_CHARS = 3500  # tránh cắt vụn các bảng 30-40 hàng vừa phải
# Mặc định cho chunk văn xuôi (body). Có thể override qua profile DB ``doffice_admin``
# (doffice_body_max_chars / doffice_body_overlap / doffice_table_max_chars) — xem
# chunker_chunking_service._chunk_doffice_document.
BODY_CHUNK_MAX_CHARS = 2800
BODY_CHUNK_OVERLAP = 300

# Nhận diện chunk "chỉ là tiêu đề mục" (document_section không có nội dung thực).
_SECTION_TITLE_ONLY_MAX_CHARS = 120
_SECTION_CONTEXT_PREFIXES = (
    "Văn bản:",
    "Ngày ban hành:",
    "Ngày văn bản:",
    "Cơ quan ban hành:",
    "Mục:",
)

CHUNK_METADATA_ALLOWLIST = {
    "reading_pos",
    "id_vb",
    "document_code",
    "ky_hieu",
    "trich_yeu",
    "issued_date",
    "ngay_vb",
    "issuer",
    "noi_ban_hanh",
    "source_type",
    "ten_file",
    "duong_dan",
    "chunk_type",
    "section_title",
    "table_name",
    "table_id",
    "logical_table_id",
    "table_title",
    "table_kind",
    "table_headers",
    "table_column",
    "table_index",
    "physical_table_index",
    "physical_tables",
    "row_index",
    "row_number",
    "row_key",
    "row_cells",
    "row_entities",
    "person_name",
    "position",
    "department",
    "phone",
    "email",
    "row_count",
    "row_start",
    "row_end",
    "column_count",
    "columns",
    "column_name",
    "column_index",
    "column_values",
    "row_context_headers",
    "group_name",
    "features",
    "platform",
    "feature_name",
    "screen_name",
    "change_content",
    "phase",
    "is_table_row",
    "is_footer_or_signature",
    "indexable",
    "embedding_enabled",
    "retrieval_priority",
    "source_summary",
    "source_span",
    "page_start",
    "page_end",
    "content_hash",
    "structure_path",
    "section_path",
    "doc_codes",
    "identifiers",
    "doc_code",
    "issuing_org",
    "document_type",
    "document_title",
    "summary",
    "entities",
    "keywords",
    "chunk_strategy",
    "subchunk_index",
    "subchunk_total",
    "quality_status",
    "quality_gate_reasons",
    "quality_warnings",
    "chapter_number",
    "chapter_title",
    "section_number",
    "article_number",
    "article_title",
    "clause_number",
    "point_label",
    "legal_path",
    "artifact_type",
    "subject",
    "answer_facts",
    "evidence_chunk_ids",
    "evidence_rows",
    "confidence",
}


def _is_section_title_only_chunk(content: str, metadata: dict[str, Any]) -> bool:
    """True nếu chunk chỉ là tiêu đề mục (document_section) mà không có nội dung thực.

    Văn bản DOffice đôi khi đánh dấu một ``document_section`` chỉ chứa tên mục (vd
    "1. CPCIT:"), nội dung thực nằm ở các mục con. Chunk như vậy gần như vô nghĩa
    khi embed và gây nhiễu retrieval. Heuristic: sau khi loại bỏ các dòng ngữ cảnh
    ("Văn bản:", "Cơ quan ban hành:", "Mục:"), phần còn lại quá ngắn VÀ trùng với
    tiêu đề mục.
    """

    if str(metadata.get("chunk_type") or "") != "document_section":
        return False
    # Heading cấu trúc của PHỤ LỤC (Phụ lục NN, "(N) F0X_...", "Mục tiêu"...) tuy ngắn
    # nhưng là ngữ cảnh cần giữ cho retrieval; không coi là tiêu đề rỗng.
    if metadata.get("artifact_type") == "appendix":
        return False

    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    body_lines = [line for line in lines if not line.startswith(_SECTION_CONTEXT_PREFIXES)]
    if not body_lines:
        return True

    body = "\n".join(body_lines)
    if len(body) >= _SECTION_TITLE_ONLY_MAX_CHARS:
        return False

    section_title = str(metadata.get("section_title") or "")
    if not section_title:
        # Suy ra tiêu đề từ dòng "Mục: <title>" nếu metadata thiếu.
        for line in lines:
            if line.startswith("Mục:"):
                section_title = line[len("Mục:") :].strip()
                break

    title_norm = section_title.strip(" :-\n").casefold()
    body_norm = body.strip(" :-\n").casefold()
    if not title_norm:
        # Không xác định được tiêu đề mà body lại rất ngắn -> coi là tiêu đề rỗng.
        return True
    return (
        body_norm == title_norm
        or body_norm.startswith(title_norm)
        or title_norm.startswith(body_norm)
    )


_CONTEXT_PREFIXES = ("Văn bản:", "Ngày ban hành:", "Cơ quan ban hành:", "Mục:")


def _appendix_full_title(content: str, metadata: dict) -> str:
    """Tiêu đề phụ lục đầy đủ từ 1 chunk preamble: section_title + dòng mô tả.

    Vd: section_title="Phụ lục 02" + body "MÔ TẢ DỮ LIỆU KHỞI TẠO..." ->
    "Phụ lục 02 — MÔ TẢ DỮ LIỆU KHỞI TẠO...".
    """
    sect = " ".join(str(metadata.get("section_title") or "").split()).strip()
    body = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith(_CONTEXT_PREFIXES)
    ]
    subtitle = [line for line in body if line.casefold() != sect.casefold()]
    return f"{sect} — {' '.join(subtitle)}" if subtitle else sect


def _merge_appendix_preamble(preamble: tuple[str, dict], content: str) -> str:
    """Gộp tiêu đề phụ lục (preamble) vào dòng ``Mục:`` của chunk kế tiếp làm mục cha."""
    title = _appendix_full_title(preamble[0], preamble[1])
    merged = re.sub(
        r"(?m)^Mục:\s*(.+)$",
        lambda m: f"Mục: {title} > {m.group(1)}",
        content,
        count=1,
    )
    return merged if merged != content else f"{title}\n\n{content}"


def build_doffice_chunks(
    normalized: NormalizedDofficeDocument,
    *,
    body_max_chars: int = BODY_CHUNK_MAX_CHARS,
    body_overlap: int = BODY_CHUNK_OVERLAP,
    table_max_chars: int = TABLE_CHUNK_MAX_CHARS,
) -> list[ChunkCreate]:
    """Sinh chunk cho văn bản DOffice.

    Builder v2 (mặc định): giữ NGUYÊN cách xử lý phần prose, nhưng BỎ table-
    explosion — mỗi bảng chỉ thành 1 (hoặc vài) chunk ``chunk_type="table"`` lấy
    từ ``normalized.tables``. Đặt env ``DOFFICE_CHUNKER_V2_ENABLED=false`` để quay
    lại builder cũ :func:`build_doffice_chunks_legacy`.

    ``body_max_chars``/``body_overlap``/``table_max_chars`` cho phép tune kích thước
    chunk qua profile DB ``doffice_admin`` (mặc định = hằng số hiện hành).
    """

    if not settings.doffice_chunker_v2_enabled:
        return build_doffice_chunks_legacy(normalized)

    chunks: list[ChunkCreate] = []

    # Tên các bảng (chuẩn hóa) -> dùng để bỏ chunk PROSE phụ lục chỉ-là-tiêu-đề mà
    # đã có chunk BẢNG cùng tên (vd "Phụ lục 01"): bảng đã mang heading + dữ liệu nên
    # chunk prose tiêu đề là trùng lặp.
    _table_names_norm = {
        " ".join(str(table.metadata.get("table_name") or "").split()).casefold().strip(" :-")
        for table in normalized.tables
    }
    _table_names_norm.discard("")

    # Tiêu đề phụ lục "Phụ lục NN" KHÔNG có bảng ngay dưới (vd Phụ lục 02) -> giữ tạm
    # để gộp vào chunk phụ lục kế tiếp, tránh chunk tiêu đề mỏng đứng riêng.
    pending_apx_preamble: tuple[str, dict] | None = None

    # --- PROSE: giữ nguyên hành vi cũ, chỉ thêm bước làm sạch cho document_body --
    for element in normalized.elements:
        chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
        if chunk_type in TABLE_VIEW_CHUNK_TYPES:
            # Bỏ các "view" bảng; bảng được xử lý riêng ở dưới.
            continue
        if chunk_type == "document_summary":
            # Bỏ chunk TÓM TẮT tổng hợp -> collection chunk chỉ giữ nội dung thật.
            continue
        prose_element = element
        if chunk_type == "document_body":
            # Làm sạch text body trước khi chunk (bỏ marker phân trang, ký tự lạ).
            prose_element = replace(element, text=clean_for_chunking(element.text))
        for content, metadata in _expanded_element_chunks(
            normalized, prose_element, body_max_chars=body_max_chars, body_overlap=body_overlap
        ):
            if not content.strip():
                continue
            metadata = _compact_chunk_metadata(metadata)
            # FIX 1: bỏ chunk chỉ là tiêu đề mục rỗng (chỉ trong builder v2).
            if _is_section_title_only_chunk(content, metadata):
                continue
            # Bỏ chunk prose phụ lục có tiêu đề trùng tên một bảng (vd "Phụ lục 01"):
            # chunk bảng đã chứa heading + dữ liệu -> prose tiêu đề là trùng.
            if metadata.get("artifact_type") == "appendix":
                _sect = " ".join(str(metadata.get("section_title") or "").split()).casefold().strip(" :-")
                _is_apx_num = bool(re.match(r"(?iu)^(phụ\s*lục|phu\s*luc)\s+\d+$", _sect))
                if _sect and (
                    _sect in _table_names_norm
                    or (_is_apx_num and any(name.startswith(_sect + " ") for name in _table_names_norm))
                ):
                    continue
                # Tiêu đề "Phụ lục NN" mỏng, không có bảng -> giữ tạm để gộp vào chunk kế.
                if _is_apx_num and len(content) < 500:
                    pending_apx_preamble = (content, metadata)
                    continue
                # Chunk phụ lục thực -> nếu có tiêu đề đang chờ, gộp vào dòng Mục:.
                if pending_apx_preamble is not None:
                    content = _merge_appendix_preamble(pending_apx_preamble, content)
                    pending_apx_preamble = None
            quality = apply_chunk_quality_gate(content, metadata)
            metadata = _compact_chunk_metadata(quality.metadata)
            metadata["reading_pos"] = _prose_reading_pos(element, metadata)
            chunks.append(
                ChunkCreate(
                    chunk_index=len(chunks),
                    content=content,
                    metadata=metadata,
                )
            )

    # Tiêu đề phụ lục đang chờ mà không có chunk phụ lục kế -> vẫn ghi ra (không mất).
    if pending_apx_preamble is not None:
        _content, _metadata = pending_apx_preamble
        _quality = apply_chunk_quality_gate(_content, _metadata)
        chunks.append(
            ChunkCreate(
                chunk_index=len(chunks),
                content=_content,
                metadata=_compact_chunk_metadata(_quality.metadata),
            )
        )

    # --- BẢNG: mỗi bảng -> 1 (hoặc vài) chunk gọn, đủ ngữ cảnh cho RAG ----------
    for table in normalized.tables:
        table_reading_pos = table.metadata.get("reading_pos")
        for sub_index, (content, metadata) in enumerate(
            _table_chunks(normalized, table, table_max_chars=table_max_chars)
        ):
            if not content.strip():
                continue
            metadata = _compact_chunk_metadata(metadata)
            quality = apply_chunk_quality_gate(content, metadata)
            metadata = _compact_chunk_metadata(quality.metadata)
            if isinstance(table_reading_pos, int):
                # +sub_index để giữ thứ tự các chunk con của cùng một bảng.
                metadata["reading_pos"] = table_reading_pos + sub_index
            chunks.append(
                ChunkCreate(
                    chunk_index=len(chunks),
                    content=content,
                    metadata=metadata,
                )
            )

    # Hai vòng prose/bảng ở trên sinh [tất cả prose] + [tất cả bảng] -> chunk_index
    # KHÔNG theo thứ tự đọc PDF (vd Phụ lục 02 prose đứng trước bảng Phụ lục 01). Sắp lại
    # theo source_span để thân bài → footer → Phụ lục 01 → Phụ lục 02 khớp văn bản gốc.
    return _reorder_chunks_by_source(chunks, doc_len=len(normalized.clean_text or ""))


def _prose_reading_pos(element: NormalizedElement, metadata: dict) -> int | None:
    """Vị trí ĐỌC (base_plain_text) của 1 chunk prose.

    Chunk phụ lục có span CỤC BỘ (so với appendix_text) -> cộng base = vị trí appendix
    trong base_plain_text (= source_span của element phụ lục). Các chunk khác dùng thẳng
    source_span.start (body theo base_clean_text ≈ tiền tố base_plain_text).
    """
    span = metadata.get("source_span") or {}
    pos = span.get("start")
    if not isinstance(pos, int):
        return None
    if metadata.get("artifact_type") == "appendix":
        base = (element.metadata.get("source_span") or {}).get("start") or 0
        return int(base) + pos
    return pos


def _reorder_chunks_by_source(chunks: list[ChunkCreate], *, doc_len: int) -> list[ChunkCreate]:
    """Sắp chunk theo THỨ TỰ ĐỌC (``reading_pos`` trong base_plain_text) rồi gán lại chunk_index.

    ``reading_pos`` đưa MỌI loại chunk (body/footer/appendix/table) về CÙNG hệ tọa độ
    base_plain_text nên sort chính xác (trước đây span ở 3 hệ khác nhau -> prose phụ lục
    & bảng chen sai chỗ).
    - ``document_summary``/``document_header`` (tổng hợp) -> ghim đầu.
    - Chunk thiếu ``reading_pos`` (hiếm) -> fallback source_span; nếu cũng fallback
      document-scope thì giữ NGAY SAU chunk thực liền trước.
    - Sort ỔN ĐỊNH theo (vị trí, thứ tự gốc). Không đổi nội dung -> overlap/embedding/ACL nguyên vẹn.
    """
    keyed: list[tuple[float, int, ChunkCreate]] = []
    last_real = 0.0
    for original_index, chunk in enumerate(chunks):
        meta = chunk.metadata or {}
        ctype = meta.get("chunk_type")
        if ctype == "document_summary":
            pos = -2.0
        elif ctype == "document_header":
            pos = -1.0
        else:
            reading_pos = meta.get("reading_pos")
            if isinstance(reading_pos, (int, float)):
                pos = float(reading_pos)
                last_real = pos
            else:
                span = meta.get("source_span") or {}
                start = span.get("start")
                is_fallback = (not isinstance(start, int)) or (
                    start == 0 and span.get("end") == doc_len and doc_len > 0
                )
                if is_fallback:
                    pos = last_real + 1e-3
                else:
                    pos = float(start)
                    last_real = pos
        keyed.append((pos, original_index, chunk))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [replace(chunk, chunk_index=new_index) for new_index, (_, _, chunk) in enumerate(keyed)]


def build_doffice_chunks_legacy(normalized: NormalizedDofficeDocument) -> list[ChunkCreate]:
    """Builder cũ: nổ mỗi bảng thành các element view (table_row/group/column...).

    Giữ y nguyên hành vi trước đây để dùng làm fallback khi tắt cờ v2.
    """

    chunks: list[ChunkCreate] = []
    for element in normalized.elements:
        for content, metadata in _expanded_element_chunks(normalized, element):
            if not content.strip():
                continue
            metadata = _compact_chunk_metadata(metadata)
            quality = apply_chunk_quality_gate(content, metadata)
            metadata = _compact_chunk_metadata(quality.metadata)
            chunks.append(
                ChunkCreate(
                    chunk_index=len(chunks),
                    content=content,
                    metadata=metadata,
                )
            )
    return chunks


def _table_document_metadata(normalized: NormalizedDofficeDocument) -> dict[str, Any]:
    """Metadata cấp văn bản mà chunk bảng cần mang theo (giống chunk prose)."""

    return {
        **_document_chunk_metadata(normalized),
        "source_type": "doffice_elasticsearch",
        "document_code": normalized.document_code,
        "doc_code": normalized.document_code,
        "ky_hieu": normalized.document_code,
        "issued_date": normalized.issued_date,
        "issuer": normalized.issuer,
        "issuing_org": normalized.issuer,
        "noi_ban_hanh": normalized.issuer,
        "document_title": normalized.title,
        "content_hash": normalized.content_hash,
    }


def _table_context_line(normalized: NormalizedDofficeDocument, table: NormalizedTable) -> str:
    """Khối ngữ cảnh đầu mỗi chunk bảng.

    Trước đây chỉ là 1 dòng ``Bảng: <tên> | Văn bản: <mã>`` nên chunk bảng nghèo ngữ
    cảnh hơn hẳn chunk prose (vốn có Ngày ban hành/Cơ quan ban hành/Mục). Giờ dựng
    khối đa dòng song song với :func:`standard_document_context` + ``Mục:`` của prose
    để chunk bảng tự mô tả đầy đủ khi retrieval độc lập.
    """

    lines: list[str] = []
    code = normalized.document_code
    title = normalized.title
    if code or title:
        lines.append(f"Văn bản: {code or ''} - {title or ''}".strip(" -"))
    if normalized.issued_date:
        lines.append(f"Ngày ban hành: {normalized.issued_date}")
    if normalized.issuer:
        lines.append(f"Cơ quan ban hành: {normalized.issuer}")
    table_name = table.metadata.get("table_name") or table.metadata.get("table_title")
    section = (
        table.metadata.get("section_title")
        or table.metadata.get("table_title")
        or table.metadata.get("table_name")
    )
    # Section thường được gán bằng chính tên bảng (parse_html_tables); chỉ thêm dòng
    # "Phụ lục/Mục" khi nó thực sự khác tên bảng để tránh lặp.
    if section and section != table_name:
        lines.append(f"Phụ lục/Mục: {section}")
    if table_name:
        lines.append(f"Bảng: {table_name}")
    else:
        lines.append("Bảng")
    return "\n".join(lines)


def _table_chunks(
    normalized: NormalizedDofficeDocument,
    table: NormalizedTable,
    *,
    table_max_chars: int = TABLE_CHUNK_MAX_CHARS,
) -> list[tuple[str, dict[str, Any]]]:
    """Sinh các chunk cho một bảng (1 chunk nếu ngắn, cắt theo dòng nếu dài)."""

    markdown = clean_for_chunking(table.markdown or "")
    if not markdown.strip():
        return []

    context_line = _table_context_line(normalized, table)
    base_metadata = {
        **_table_document_metadata(normalized),
        "chunk_type": "table",
        "table_id": table.metadata.get("table_id"),
        "logical_table_id": table.metadata.get("logical_table_id"),
        "table_name": table.metadata.get("table_name"),
        "table_title": table.metadata.get("table_title") or table.metadata.get("table_name"),
        "table_kind": table.metadata.get("table_kind"),
        "table_index": table.metadata.get("table_index"),
        "table_headers": list(table.headers) if table.headers else None,
        "section_title": table.metadata.get("section_title"),
        "row_count": table.metadata.get("row_count"),
        "column_count": table.metadata.get("column_count"),
        "source_span": table.metadata.get("source_span"),
        "indexable": True,
        "embedding_enabled": True,
    }

    pieces: list[str]
    if len(markdown) <= table_max_chars:
        pieces = [markdown]
    else:
        pieces = _split_table_markdown(markdown, table_max_chars=table_max_chars)

    results: list[tuple[str, dict[str, Any]]] = []
    total = len(pieces)
    for index, piece in enumerate(pieces):
        # FIX 2A: chunk phần 2+ vốn không có dòng tên bảng -> thêm "Phần X/Y" để
        # mỗi mảnh vẫn đủ ngữ cảnh khi retrieval độc lập. context_line giờ là khối
        # đa dòng nên gắn thông tin phần thành dòng riêng thay vì nối đuôi.
        if total > 1:
            chunk_context_line = f"{context_line}\nPhần: {index + 1}/{total}"
        else:
            chunk_context_line = context_line
        content = f"{chunk_context_line}\n{piece}".strip()
        metadata = dict(base_metadata)
        if total > 1:
            metadata["subchunk_index"] = index
            metadata["subchunk_total"] = total
            metadata["chunk_strategy"] = "table_chunker_split"
        else:
            metadata["chunk_strategy"] = "table_single"
        results.append((content, metadata))
    return results


def _split_table_markdown(markdown: str, *, table_max_chars: int = TABLE_CHUNK_MAX_CHARS) -> list[str]:
    """Cắt markdown bảng dài theo dòng bằng chonkie.TableChunker (giữ header)."""

    from chonkie import TableChunker

    rows_per_chunk = _rows_per_chunk(markdown, table_max_chars=table_max_chars)
    try:
        chunker = TableChunker(chunk_size=rows_per_chunk)
        pieces = [chunk.text.strip() for chunk in chunker(markdown) if chunk.text.strip()]
    except Exception:
        pieces = []
    # FIX 2C: tránh mảnh cuối chỉ có 1-2 hàng dữ liệu (embedding rất kém) -> gộp
    # phần dữ liệu của mảnh cuối vào mảnh liền trước.
    pieces = _merge_tiny_last_table_piece(pieces)
    # Phòng trường hợp TableChunker không cắt được (vd markdown không chuẩn) ->
    # fallback giữ nguyên 1 mảnh để không mất dữ liệu.
    return pieces or [markdown]


# Số hàng dữ liệu tối thiểu để mảnh bảng cuối được đứng riêng.
_TABLE_MIN_LAST_DATA_ROWS = 3


def _merge_tiny_last_table_piece(pieces: list[str]) -> list[str]:
    """Gộp mảnh bảng cuối vào mảnh trước nếu nó quá ngắn (< 3 hàng dữ liệu).

    ``TableChunker`` lặp lại 2 dòng đầu (header + dòng phân cách ``| --- |``) ở mỗi
    mảnh, nên hàng dữ liệu của mảnh = các dòng từ vị trí 3 trở đi.
    """

    if len(pieces) < 2:
        return pieces

    last_lines = [line for line in pieces[-1].splitlines() if line.strip()]
    data_rows = last_lines[2:] if len(last_lines) > 2 else []
    if len(data_rows) >= _TABLE_MIN_LAST_DATA_ROWS:
        return pieces
    if not data_rows:
        # Mảnh cuối chỉ có header, không có dữ liệu -> bỏ hẳn.
        return pieces[:-1]
    merged = pieces[-2] + "\n" + "\n".join(data_rows)
    return pieces[:-2] + [merged]


def _rows_per_chunk(markdown: str, *, table_max_chars: int = TABLE_CHUNK_MAX_CHARS) -> int:
    """Ước lượng số dòng/chunk sao cho mỗi mảnh ~<= table_max_chars."""

    lines = [line for line in markdown.split("\n") if line.strip()]
    if len(lines) <= 3:
        return max(1, len(lines))
    # Hai dòng đầu thường là header + dòng phân cách "| --- |".
    header_len = sum(len(line) + 1 for line in lines[:2])
    data_lines = lines[2:]
    avg_row_len = max(1, sum(len(line) + 1 for line in data_lines) // max(1, len(data_lines)))
    budget = max(1, table_max_chars - header_len)
    return max(1, budget // avg_row_len)


def _expanded_element_chunks(
    normalized: NormalizedDofficeDocument,
    element: NormalizedElement,
    *,
    body_max_chars: int = BODY_CHUNK_MAX_CHARS,
    body_overlap: int = BODY_CHUNK_OVERLAP,
) -> list[tuple[str, dict[str, Any]]]:
    chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
    base_metadata = {
        **_document_chunk_metadata(normalized),
        **element.metadata,
        "source_type": "doffice_elasticsearch",
        "chunk_type": chunk_type,
        "document_code": normalized.document_code,
        "doc_code": normalized.document_code,
        "ky_hieu": normalized.document_code,
        # FIX 3B: ưu tiên issued_date cấp văn bản, fallback về element metadata.
        "issued_date": normalized.issued_date or element.metadata.get("issued_date"),
        "issuer": normalized.issuer,
        "issuing_org": normalized.issuer,
        "noi_ban_hanh": normalized.issuer,
        "document_title": normalized.title,
        "summary": element.metadata.get("summary"),
        "table_title": element.metadata.get("table_title") or element.metadata.get("table_name"),
        "table_headers": element.metadata.get("table_headers") or element.metadata.get("headers"),
            "columns": element.metadata.get("columns"),
            "source_span": element.metadata.get("source_span"),
        "source_summary": chunk_type == "document_summary",
        "is_table_row": chunk_type == "table_row",
        "is_footer_or_signature": chunk_type == "footer_signature",
        "indexable": bool(element.metadata.get("indexable", chunk_type != "footer_signature")),
        "embedding_enabled": bool(element.metadata.get("embedding_enabled", element.metadata.get("indexable", chunk_type != "footer_signature"))),
        "content_hash": normalized.content_hash,
        "structure_path": _structure_path(element),
    }
    if chunk_type == "document_body":
        evidence_chunks = build_body_evidence_chunks(
            text=element.text,
            base_metadata=base_metadata,
            max_chars=body_max_chars,
            overlap_chars=body_overlap,
        )
        if evidence_chunks:
            return [(chunk.content, chunk.metadata) for chunk in evidence_chunks]
    content = _element_content(element)
    return [(content, base_metadata)]

def _document_chunk_metadata(normalized: NormalizedDofficeDocument) -> dict[str, object]:
    return {
        key: normalized.metadata.get(key)
        for key in (
            "id_vb",
            "document_code",
            "ky_hieu",
            "trich_yeu",
            "issued_date",
            "ngay_vb",
            "issuer",
            "noi_ban_hanh",
            "source_type",
            "ten_file",
            "duong_dan",
            "doc_codes",
            "identifiers",
        )
    }

def _compact_chunk_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key in CHUNK_METADATA_ALLOWLIST and value not in (None, "", [])
    }


def _element_content(element: NormalizedElement) -> str:
    chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
    if chunk_type == "footer_signature":
        return element.text.strip()
    lines = []
    if chunk_type not in {"document_header", "document_summary"}:
        document_code = element.metadata.get("document_code") or element.metadata.get("ky_hieu")
        title = element.metadata.get("trich_yeu")
        if document_code or title:
            lines.append(f"Văn bản: {document_code or ''} - {title or ''}".strip(" -"))
    lines.append(element.text.strip())
    return "\n".join(line for line in lines if line.strip())


def _structure_path(element: NormalizedElement) -> list[str]:
    path = [str(element.metadata.get("chunk_type") or element.element_type)]
    for key in ("platform", "phase", "feature_name"):
        value = element.metadata.get(key)
        if value:
            path.append(str(value))
    return path
