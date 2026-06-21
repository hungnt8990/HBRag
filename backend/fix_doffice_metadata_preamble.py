from pathlib import Path

ROOT = Path.cwd()
normalizer_path = ROOT / "app" / "services" / "doffice_content_normalizer.py"
chunking_path = ROOT / "app" / "services" / "doffice_chunking.py"

if not normalizer_path.exists() or not chunking_path.exists():
    raise SystemExit("Run this script from backend root. Expected app/services/doffice_content_normalizer.py and app/services/doffice_chunking.py")

# 1) Enrich normalized DOffice content with authoritative source properties.
text = normalizer_path.read_text(encoding="utf-8")

old = '''def normalize_doffice_source(source: dict[str, Any]) -> NormalizedDofficeDocument:
    raw_text = str(source.get("noi_dung") or "")
    summary_text = compact_source_summary(str(source.get("tom_tat") or "").strip()) or None
    tables = parse_html_tables(raw_text)
    markdown_text = replace_tables(raw_text, tables, replacement="markdown")
    plain_text = html_to_plain_text(replace_tables(raw_text, tables, replacement="placeholder"))
    body_text, footer_text = split_footer_signature(plain_text)
    clean_text = normalize_lines(apply_spacing_fixes(strip_markdown_noise(body_text)))
    metadata = build_rule_metadata(source=source, clean_text=clean_text, tables=tables)
    elements = build_elements(source=source, clean_text=clean_text, tables=tables, footer_text=footer_text, summary_text=summary_text, metadata=metadata)
    content_hash = sha256_text(raw_text)
    metadata_hash = sha256_json({key: source.get(key) for key in sorted(source) if key != "noi_dung"})

    return NormalizedDofficeDocument(
'''
new = '''def normalize_doffice_source(source: dict[str, Any]) -> NormalizedDofficeDocument:
    raw_text = str(source.get("noi_dung") or "")
    summary_text = compact_source_summary(str(source.get("tom_tat") or "").strip()) or None
    tables = parse_html_tables(raw_text)
    base_markdown_text = replace_tables(raw_text, tables, replacement="markdown")
    base_plain_text = html_to_plain_text(replace_tables(raw_text, tables, replacement="placeholder"))
    body_text, footer_text = split_footer_signature(base_plain_text)
    base_clean_text = normalize_lines(apply_spacing_fixes(strip_markdown_noise(body_text)))
    metadata = build_rule_metadata(source=source, clean_text=base_clean_text, tables=tables)
    metadata_preamble = build_doffice_metadata_preamble(source=source, metadata=metadata)
    metadata = {**metadata, "metadata_preamble": metadata_preamble}
    clean_text = prepend_metadata_preamble(base_clean_text, metadata_preamble)
    plain_text = prepend_metadata_preamble(base_plain_text, metadata_preamble)
    markdown_text = prepend_metadata_preamble(base_markdown_text, metadata_preamble)
    elements = build_elements(source=source, clean_text=base_clean_text, tables=tables, footer_text=footer_text, summary_text=summary_text, metadata=metadata)
    content_hash = sha256_text("\n\n".join(part for part in (metadata_preamble, raw_text) if part.strip()))
    metadata_hash = sha256_json({key: source.get(key) for key in sorted(source) if key != "noi_dung"})

    return NormalizedDofficeDocument(
'''
if old in text:
    text = text.replace(old, new)
elif "build_doffice_metadata_preamble" not in text:
    raise SystemExit("Could not find normalize_doffice_source block to patch. Please send app/services/doffice_content_normalizer.py")

helpers = r'''

def build_doffice_metadata_preamble(*, source: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Build authoritative document metadata text and prepend it to DOffice content.

    Some DOffice OCR text loses values in the body, for example ``Số: /EVNICT-TTPM``
    while the source properties still contain ``ky_hieu`` and ``ngay_vb``. The preamble
    makes those authoritative properties available to chunking, enrichment, embedding,
    BM25, and answer generation.
    """
    document_code = _optional_string(source.get("ky_hieu")) or _optional_string(metadata.get("document_code"))
    issued_date = normalize_date(source.get("ngay_vb")) or _optional_string(metadata.get("issued_date"))
    lines = ["THÔNG TIN VĂN BẢN DOFFICE"]
    for label, value in (
        ("ID_VB", source.get("id_vb") or metadata.get("id_vb")),
        ("Số/ký hiệu văn bản", document_code),
        ("Ngày văn bản", issued_date),
        ("Trích yếu", source.get("trich_yeu") or metadata.get("trich_yeu")),
        ("Nơi ban hành", source.get("noi_ban_hanh") or metadata.get("noi_ban_hanh") or metadata.get("issuer")),
        ("Người ký", source.get("nguoi_ky") or metadata.get("nguoi_ky")),
        ("Tên file", source.get("ten_file") or metadata.get("ten_file")),
        ("Đường dẫn", source.get("duong_dan") or metadata.get("duong_dan")),
        ("Năm", source.get("nam") or metadata.get("nam")),
        ("Tháng", source.get("thang") or metadata.get("thang")),
    ):
        clean_value = _optional_string(value)
        if clean_value:
            lines.append(f"{label}: {clean_value}")
    return "\n".join(lines)


def prepend_metadata_preamble(text: str, preamble: str) -> str:
    clean_text = normalize_lines(str(text or ""))
    clean_preamble = normalize_lines(str(preamble or ""))
    if not clean_preamble:
        return clean_text
    if clean_text.startswith(clean_preamble):
        return clean_text
    return "\n\n".join(part for part in (clean_preamble, clean_text) if part.strip())
'''
if "def build_doffice_metadata_preamble" not in text:
    marker = "\n\ndef parse_html_tables(raw_text: str) -> list[NormalizedTable]:\n"
    if marker not in text:
        raise SystemExit("Could not insert metadata preamble helpers before parse_html_tables")
    text = text.replace(marker, helpers + marker)

old_header = '''    header_text = "\n".join(
        part
        for part in (
            f"Số/ký hiệu: {metadata.get('document_code') or source.get('ky_hieu') or ''}",
            f"Ngày văn bản: {metadata.get('issued_date') or source.get('ngay_vb') or ''}",
            f"Trích yếu: {source.get('trich_yeu') or ''}",
            f"Nơi ban hành: {source.get('noi_ban_hanh') or ''}",
            f"Người ký: {source.get('nguoi_ky') or ''}",
        )
        if part.rsplit(":", 1)[-1].strip()
    )
'''
new_header = '''    header_text = str(metadata.get("metadata_preamble") or "").strip() or "\n".join(
        part
        for part in (
            f"Số/ký hiệu: {metadata.get('document_code') or source.get('ky_hieu') or ''}",
            f"Ngày văn bản: {metadata.get('issued_date') or source.get('ngay_vb') or ''}",
            f"Trích yếu: {source.get('trich_yeu') or ''}",
            f"Nơi ban hành: {source.get('noi_ban_hanh') or ''}",
            f"Người ký: {source.get('nguoi_ky') or ''}",
        )
        if part.rsplit(":", 1)[-1].strip()
    )
'''
if old_header in text:
    text = text.replace(old_header, new_header)
normalizer_path.write_text(text, encoding="utf-8")

# 2) Put the metadata context into every indexable DOffice chunk, so embeddings and BM25 include code/date/id_vb.
text = chunking_path.read_text(encoding="utf-8")
if '"metadata_preamble",' not in text:
    text = text.replace('''    "source_summary",\n    "content_hash",''', '''    "source_summary",\n    "metadata_preamble",\n    "content_hash",''')

text = text.replace(
    '''        content = _element_content(element)\n''',
    '''        document_metadata = _document_chunk_metadata(normalized)\n'''
    '''        content = _element_content(element, document_metadata=document_metadata)\n''',
)
text = text.replace('''                **_document_chunk_metadata(normalized),\n                **element.metadata,''', '''                **document_metadata,\n                **element.metadata,''')
text = text.replace('''            "doc_codes",\n            "identifiers",''', '''            "doc_codes",\n            "identifiers",\n            "metadata_preamble",''')

old_func = '''def _element_content(element: NormalizedElement) -> str:
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
'''
new_func = '''def _element_content(element: NormalizedElement, *, document_metadata: dict[str, object]) -> str:
    chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
    if chunk_type == "footer_signature":
        return element.text.strip()
    lines = []
    if chunk_type != "document_header":
        context = _chunk_document_context({**document_metadata, **element.metadata})
        if context:
            lines.extend(context)
    lines.append(element.text.strip())
    return "\n".join(line for line in lines if line.strip())


def _chunk_document_context(metadata: dict[str, object]) -> list[str]:
    document_code = metadata.get("document_code") or metadata.get("ky_hieu") or metadata.get("doc_code")
    issued_date = metadata.get("issued_date") or metadata.get("ngay_vb")
    id_vb = metadata.get("id_vb")
    title = metadata.get("trich_yeu")
    issuer = metadata.get("issuer") or metadata.get("noi_ban_hanh")
    first_line_parts = []
    if document_code:
        first_line_parts.append(f"Số/ký hiệu: {document_code}")
    if issued_date:
        first_line_parts.append(f"Ngày văn bản: {issued_date}")
    if id_vb:
        first_line_parts.append(f"ID_VB: {id_vb}")
    lines = [" | ".join(first_line_parts)] if first_line_parts else []
    if title:
        lines.append(f"Trích yếu: {title}")
    if issuer:
        lines.append(f"Nơi ban hành: {issuer}")
    return lines
'''
if old_func in text:
    text = text.replace(old_func, new_func)
elif "def _chunk_document_context" not in text:
    raise SystemExit("Could not find _element_content function to patch. Please send app/services/doffice_chunking.py")

chunking_path.write_text(text, encoding="utf-8")

print("Done: DOffice metadata preamble is now inserted into parsed content and every indexable chunk.")
