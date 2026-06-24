from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from pydantic import BaseModel


def _stub_modules() -> dict[str, ModuleType]:
    modules = {
        "docling": ModuleType("docling"),
        "docling.chunking": ModuleType("docling.chunking"),
        "docling_core": ModuleType("docling_core"),
        "docling_core.transforms": ModuleType("docling_core.transforms"),
        "docling_core.transforms.chunker": ModuleType("docling_core.transforms.chunker"),
        "docling_core.transforms.chunker.hierarchical_chunker": ModuleType(
            "docling_core.transforms.chunker.hierarchical_chunker"
        ),
        "docling_core.transforms.chunker.tokenizer": ModuleType(
            "docling_core.transforms.chunker.tokenizer"
        ),
        "docling_core.transforms.chunker.tokenizer.base": ModuleType(
            "docling_core.transforms.chunker.tokenizer.base"
        ),
        "docling_core.transforms.serializer": ModuleType(
            "docling_core.transforms.serializer"
        ),
        "docling_core.transforms.serializer.markdown": ModuleType(
            "docling_core.transforms.serializer.markdown"
        ),
        "docling_core.types": ModuleType("docling_core.types"),
        "docling_core.types.doc": ModuleType("docling_core.types.doc"),
    }

    class Dummy:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class BaseTokenizer(BaseModel):
        pass

    modules["docling.chunking"].HybridChunker = Dummy
    hierarchical = modules[
        "docling_core.transforms.chunker.hierarchical_chunker"
    ]
    hierarchical.ChunkingDocSerializer = Dummy
    hierarchical.ChunkingSerializerProvider = Dummy
    modules["docling_core.transforms.chunker.tokenizer.base"].BaseTokenizer = BaseTokenizer
    modules["docling_core.transforms.serializer.markdown"].MarkdownParams = Dummy
    modules["docling_core.transforms.serializer.markdown"].MarkdownTableSerializer = Dummy
    modules["docling_core.types.doc"].DoclingDocument = Dummy
    return modules


def _load_docling_v6_module():
    if importlib.util.find_spec("docling") is not None:
        from app.services.chunkers import chunker_docling_v6_chunking

        return chunker_docling_v6_chunking

    stubs = _stub_modules()
    previous = {name: sys.modules.get(name) for name in stubs}
    try:
        sys.modules.update(stubs)
        module_path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "services"
            / "chunkers"
            / "chunker_docling_v6_chunking.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_test_docling_v6_chunking", module_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


_docling_v6 = _load_docling_v6_module()
repair_cross_page_table_continuations = (
    _docling_v6.repair_cross_page_table_continuations
)
detect_document_profile = _docling_v6.detect_document_profile
RegexVietnameseTokenizer = _docling_v6.RegexVietnameseTokenizer
normalize_vietnamese_pdf_text = _docling_v6.normalize_vietnamese_pdf_text
strip_image_placeholders = _docling_v6.strip_image_placeholders
add_table_parent_chunks = _docling_v6.add_table_parent_chunks
finalize_record_metadata = _docling_v6.finalize_record_metadata
reindex_records = _docling_v6.reindex_records
repack_table_group = _docling_v6.repack_table_group
repair_records = _docling_v6.repair_records
repair_table_identity = _docling_v6.repair_table_identity
resolve_cross_references = _docling_v6.resolve_cross_references
semantic_validation_issues = _docling_v6.semantic_validation_issues
synchronize_record_provenance = _docling_v6.synchronize_record_provenance

def test_explicit_attribute_table_name_resets_stale_f10_context() -> None:
    text = """3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh
(7) F10_TuPhanPhoi_HT - Lá»›p tá»§ phÃ¢n phá»‘i
(1) HinhAnhCotDien - HÃ¬nh áº£nh cá»™t Ä‘iá»‡n
TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien
MÃ´ táº£ Ä‘Æ°á»ng dáº«n hÃ¬nh áº£nh cá»™t Ä‘iá»‡n
| TT | TÃªn trÆ°á»ng | MÃ´ táº£ | Kiá»ƒu dá»¯ liá»‡u | Miá»n giÃ¡ trá»‹ | Äá»™ rá»™ng |
|---|---|---|---|---|---|
| 1 | IDHinhAnh | ID hÃ¬nh áº£nh | Text | | 50 |
| 2 | IDCotDien | ID cá»™t Ä‘iá»‡n | Text | | 50 |"""
    records = repair_table_identity(
        [
            {
                "contextualized_text": text,
                "text": text,
                "table_name": "F10_TuPhanPhoi_HT",
                "section_path": [
                    "3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh",
                    "(7) F10_TuPhanPhoi_HT - Lá»›p tá»§ phÃ¢n phá»‘i",
                ],
                "headings": [],
                "pages": [9, 10],
            }
        ]
    )

    record = records[0]
    assert record["table_name"] == "HinhAnhCotDien"
    assert "F10_TuPhanPhoi_HT" not in record["contextualized_text"]
    assert all("F10_TuPhanPhoi_HT" not in item for item in record["section_path"])
    assert record["section_path"][-1].startswith("(1) HinhAnhCotDien")
    assert record["repair_metadata"]["table_identity_replaced"]["to"] == "HinhAnhCotDien"


def test_full_repair_pipeline_does_not_carry_f10_into_hinhanhcotdien() -> None:
    f10_text = """(7) F10_TuPhanPhoi_HT - Lá»›p tá»§ phÃ¢n phá»‘i
| TT | TrÆ°á»ng dá»¯ liá»‡u | MÃ´ táº£ | Kiá»ƒu dá»¯ liá»‡u | Nguá»“n dá»¯ liá»‡u |
|---|---|---|---|---|
| 1 | ID | ID tá»§ háº¡ tháº¿ | Text | ID tá»± sinh cá»§a GIS |"""
    attribute_text = """3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh
(7) F10_TuPhanPhoi_HT - Lá»›p tá»§ phÃ¢n phá»‘i
(1) HinhAnhCotDien - HÃ¬nh áº£nh cá»™t Ä‘iá»‡n
TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien
MÃ´ táº£ Ä‘Æ°á»ng dáº«n hÃ¬nh áº£nh cá»™t Ä‘iá»‡n
| TT | TÃªn trÆ°á»ng | MÃ´ táº£ | Kiá»ƒu dá»¯ liá»‡u | Äá»™ rá»™ng |
|---|---|---|---|---|
| 1 | IDHinhAnh | ID hÃ¬nh áº£nh | Text | 50 |
| 2 | IDCotDien | ID cá»™t Ä‘iá»‡n | Text | 50 |"""
    records = []
    for index, text in enumerate((f10_text, attribute_text), start=1):
        records.append(
            {
                "chunk_id": f"raw_{index:03d}",
                "chunk_type": "docling_hybrid_repaired",
                "pages": [9, 10],
                "headings": [],
                "raw_text": text,
                "raw_contextualized_text": text,
                "text": text,
                "contextualized_text": text,
                "doc_item_types": ["table"],
                "document_context": "",
            }
        )

    repaired = repair_records(
        records,
        doc=SimpleNamespace(texts=[]),
        tokenizer=RegexVietnameseTokenizer(max_tokens=350),
        max_tokens=350,
    )

    attribute = next(
        record for record in repaired if record.get("table_name") == "HinhAnhCotDien"
    )
    assert "F10_TuPhanPhoi_HT" not in attribute["text"]
    assert attribute["raw_text"] == attribute["text"]
    assert attribute["quality_status"] == "pass"
    assert attribute["validation_issues"] == []


def test_final_provenance_keeps_parser_source_but_aligns_raw_text_to_chunk() -> None:
    record = {
        "contextualized_text": "1.2. GIS háº¡ tháº¿:\nNá»™i dung Ä‘Ã£ sá»­a.",
        "text": "old",
        "raw_text": "má»™t block nguá»“n dÃ i chá»©a cáº£ má»¥c 1.2 vÃ  2.1",
        "pages": [1, 2],
    }

    result = synchronize_record_provenance([record])[0]

    assert result["raw_text"] == result["text"]
    assert result["normalized_text"] == result["text"]
    assert result["source_raw_text"] == "má»™t block nguá»“n dÃ i chá»©a cáº£ má»¥c 1.2 vÃ  2.1"
    assert result["provenance_status"] == "chunk_aligned"


def test_table_repacking_avoids_a_tiny_tail() -> None:
    prefix = ["(1) F05_CongToKhachHang_HT - Lá»›p cÃ´ng tÆ¡ khÃ¡ch hÃ ng"]
    header = [
        "| TT | TrÆ°á»ng dá»¯ liá»‡u | MÃ´ táº£ | Kiá»ƒu dá»¯ liá»‡u | Nguá»“n dá»¯ liá»‡u |",
        "|---|---|---|---|---|",
    ]
    rows = [
        f"| {index} | Field{index} | MÃ´ táº£ trÆ°á»ng dá»¯ liá»‡u sá»‘ {index} cÃ³ ná»™i dung | Text | CMIS |"
        for index in range(1, 25)
    ]
    groups = []
    for start, end in ((0, 10), (10, 21), (21, 24)):
        text = "\n".join(prefix + header + rows[start:end])
        groups.append(
            {
                "contextualized_text": text,
                "text": text,
                "pages": [6, 7],
                "section_path": prefix,
            }
        )

    tokenizer = RegexVietnameseTokenizer(max_tokens=150)
    packed = repack_table_group(groups, tokenizer, max_tokens=150)
    row_counts = [
        sum(
            1
            for line in record["contextualized_text"].splitlines()
            if line.startswith("| ") and line.split("|")[1].strip().isdigit()
        )
        for record in packed
    ]

    assert min(row_counts) >= 3
    assert all(tokenizer.count_tokens(record["contextualized_text"]) <= 150 for record in packed)


def test_table_parent_is_inserted_and_children_point_to_it() -> None:
    children = [
        {
            "contextualized_text": "F08 part 1",
            "text": "F08 part 1",
            "raw_text": "F08 part 1",
            "table_name": "F08_CotDien_HT",
            "table_description": "Lá»›p cá»™t Ä‘iá»‡n",
            "chunk_type": "table_rows",
            "content_format": "markdown_table",
            "field_names": ["ID", "MaTramBienAp"],
            "source_systems": ["GIS", "TTHT"],
            "convertible_fields": ["MaTramBienAp"],
            "row_start": 1,
            "row_end": 2,
            "pages": [5],
            "section_path": ["F08_CotDien_HT - Lá»›p cá»™t Ä‘iá»‡n"],
            "headings": ["F08_CotDien_HT - Lá»›p cá»™t Ä‘iá»‡n"],
        },
        {
            "contextualized_text": "F08 part 2",
            "text": "F08 part 2",
            "raw_text": "F08 part 2",
            "table_name": "F08_CotDien_HT",
            "table_description": "Lá»›p cá»™t Ä‘iá»‡n",
            "chunk_type": "table_rows",
            "content_format": "markdown_table",
            "field_names": ["X", "Y"],
            "source_systems": ["TTHT"],
            "convertible_fields": ["X", "Y"],
            "row_start": 3,
            "row_end": 4,
            "pages": [6],
            "section_path": ["F08_CotDien_HT - Lá»›p cá»™t Ä‘iá»‡n"],
            "headings": ["F08_CotDien_HT - Lá»›p cá»™t Ä‘iá»‡n"],
        },
    ]

    records = add_table_parent_chunks(children)
    records = synchronize_record_provenance(records)
    records = finalize_record_metadata(records)
    records = reindex_records(records, RegexVietnameseTokenizer(max_tokens=350))

    parent = next(record for record in records if record["chunk_type"] == "table_parent")
    child_records = [record for record in records if record["chunk_type"] == "table_rows"]
    assert parent["field_names"] == ["ID", "MaTramBienAp", "X", "Y"]
    assert all(record["parent_chunk_id"] == parent["chunk_id"] for record in child_records)


def test_cross_reference_is_resolved_to_section_text() -> None:
    records = [
        {
            "contextualized_text": "2.2. GIS háº¡ tháº¿:\nHoÃ n thÃ nh trong thÃ¡ng 9/2026.",
            "section_path": ["2. CÃ¡c CTÄL", "2.2. GIS háº¡ tháº¿"],
        },
        {
            "contextualized_text": "3.2. GIS háº¡ tháº¿:\nTriá»ƒn khai theo káº¿ hoáº¡ch táº¡i má»¥c 2.2.",
            "section_path": ["3. KHoPC", "3.2. GIS háº¡ tháº¿"],
        },
    ]

    resolved = resolve_cross_references(records)

    assert resolved[1]["cross_references"] == ["2.2"]
    assert "thÃ¡ng 9/2026" in resolved[1]["resolved_reference_text"]


def test_semantic_validator_rejects_table_metadata_mismatch() -> None:
    text = """TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien
| TT | TÃªn trÆ°á»ng |
|---|---|
| 1 | IDHinhAnh |"""
    issues = semantic_validation_issues(
        {
            "contextualized_text": text,
            "raw_text": text,
            "table_name": "F10_TuPhanPhoi_HT",
            "chunk_type": "table_rows",
            "section_path": ["(7) F10_TuPhanPhoi_HT - Lá»›p tá»§ phÃ¢n phá»‘i"],
        }
    )

    assert {
        "table_name_content_mismatch",
        "section_path_table_mismatch",
        "stale_table_state",
    }.issubset({issue["type"] for issue in issues})
    assert all(issue["severity"] == "critical" for issue in issues)


def test_single_small_table_becomes_complete_without_parent() -> None:
    table_text = (
        "TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien\n"
        "| TT | TÃªn trÆ°á»ng |\n"
        "|---|---|\n"
        "| 1 | IDHinhAnh |"
    )
    child = {
        "contextualized_text": table_text,
        "text": table_text,
        "raw_text": table_text,
        "table_name": "F10_TuPhanPhoi_HT",
        "entity": "F10_TuPhanPhoi_HT",
        "chunk_type": "table_rows",
        "field_names": ["IDHinhAnh"],
        "pages": [10],
        "section_path": ["3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh"],
    }

    records = add_table_parent_chunks([child])

    assert len(records) == 1
    assert records[0]["chunk_type"] == "table_complete"
    assert records[0]["table_name"] == "HinhAnhCotDien"
    assert records[0]["entity"] == "HinhAnhCotDien"
    assert "parent_chunk_id" not in records[0]


def test_split_table_grouping_is_content_first_not_stale_metadata() -> None:
    base = {
        "chunk_type": "table_rows",
        "field_names": ["ID"],
        "pages": [10],
        "section_path": ["3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh"],
        "table_name": "F10_TuPhanPhoi_HT",
        "entity": "F10_TuPhanPhoi_HT",
    }
    first_text = (
        "TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien\n"
        "| TT | TÃªn trÆ°á»ng |\n"
        "|---|---|\n"
        "| 1 | ID |"
    )
    first = {
        **base,
        "contextualized_text": first_text,
        "text": "x",
        "raw_text": "x",
        "row_start": 1,
        "row_end": 1,
    }
    second_text = (
        "TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien\n"
        "| TT | TÃªn trÆ°á»ng |\n"
        "|---|---|\n"
        "| 2 | IDCotDien |"
    )
    second = {
        **base,
        "contextualized_text": second_text,
        "text": "y",
        "raw_text": "y",
        "field_names": ["IDCotDien"],
        "row_start": 2,
        "row_end": 2,
    }

    records = add_table_parent_chunks([first, second])
    parent = next(record for record in records if record["chunk_type"] == "table_parent")
    children = [record for record in records if record["chunk_type"] == "table_rows"]

    assert parent["table_name"] == "HinhAnhCotDien"
    assert all(record["table_name"] == "HinhAnhCotDien" for record in children)
    assert all(
        record["_parent_record_key"] == "table_parent::hinhanhcotdien"
        for record in children
    )


def test_normalize_vietnamese_pdf_text_repairs_slide_glyph_spacing() -> None:
    raw = "CÃ”NG C á»¤ ÄÃ” NG B á»˜ D á»® LI á»† U GIS H áº  THÃŠ"
    assert normalize_vietnamese_pdf_text(raw) == (
        "CÃ”NG Cá»¤ Äá»’NG Bá»˜ Dá»® LIá»†U GIS Háº  THáº¾"
    )


def test_normalize_vietnamese_pdf_text_keeps_real_word_boundaries() -> None:
    raw = "CÄƒn c á»© vb s á»‘ 6515 vÃ  L á»› p thi áº¿ t b á»‹ Ä‘Ã³ng c áº¯ t"
    assert normalize_vietnamese_pdf_text(raw) == (
        "CÄƒn cá»© vb sá»‘ 6515 vÃ  Lá»›p thiáº¿t bá»‹ Ä‘Ã³ng cáº¯t"
    )


def test_strip_image_placeholders_removes_visual_only_markers() -> None:
    assert strip_image_placeholders(
        "GIá»šI THIá»†U CÃ”NG Cá»¤\n<!-- image -->\n<!-- image -->"
    ) == "GIá»šI THIá»†U CÃ”NG Cá»¤"


def test_normalize_vietnamese_pdf_text_keeps_space_before_accented_word() -> None:
    assert normalize_vietnamese_pdf_text("Ä‘Ã£ cÃ³ á»Ÿ cÃ¡c pháº§n má»m") == (
        "Ä‘Ã£ cÃ³ á»Ÿ cÃ¡c pháº§n má»m"
    )
    assert normalize_vietnamese_pdf_text("chuyá»ƒn Ä‘á»•i á»Ÿ giai Ä‘oáº¡n sau") == (
        "chuyá»ƒn Ä‘á»•i á»Ÿ giai Ä‘oáº¡n sau"
    )


def test_cross_page_table_carries_merged_leading_cells() -> None:
    first = {
        "contextualized_text": (
            "PHá»¤ Lá»¤C\n"
            "| STT | Há»‡ thá»‘ng | TÃ¬nh tráº¡ng | NguyÃªn nhÃ¢n | "
            "ÄÃ£ xá»­ lÃ½ | YÃªu cáº§u thá»±c hiá»‡n |\n"
            "|---|---|---|---|---|---|\n"
            "| 3 | Web/app CSKH | Hiá»ƒn thá»‹ sai | Nguá»“n dá»¯ liá»‡u sai | ÄÃ£ Ä‘á»“ng bá»™ láº¡i | Láº¥y tá»« CMIS |"
        ),
        "text": "",
        "pages": [3],
        "section_path": ["PHá»¤ Lá»¤C"],
    }
    second = {
        "contextualized_text": (
            "| STT | Há»‡ thá»‘ng | TÃ¬nh tráº¡ng | NguyÃªn nhÃ¢n | "
            "ÄÃ£ xá»­ lÃ½ | YÃªu cáº§u thá»±c hiá»‡n |\n"
            "|---|---|---|---|---|---|\n"
            "|  |  | KhÃ´ng hiá»ƒn thá»‹ hÃ³a Ä‘Æ¡n | DÃ¹ng chá»‰ sá»‘ chá»‘t cÅ© | "
            "ÄÃ£ Ä‘á»“ng bá»™ láº¡i | Láº¥y hÃ³a Ä‘Æ¡n tá»« CMIS |\n"
            "|  |  | GiÃ¡n Ä‘oáº¡n dá»‹ch vá»¥ | Thiáº¿u HA DC-DR | ÄÃ£ khÃ´i phá»¥c | HoÃ n thiá»‡n HA |"
        ),
        "text": "",
        "pages": [4],
        "section_path": [],
    }

    repaired = repair_cross_page_table_continuations([first, second])

    assert repaired[1]["cross_page_table_continuation"] is True
    assert "| 3 | Web/app CSKH | KhÃ´ng hiá»ƒn thá»‹ hÃ³a Ä‘Æ¡n" in repaired[1][
        "contextualized_text"
    ]
    assert "| 3 | Web/app CSKH | GiÃ¡n Ä‘oáº¡n dá»‹ch vá»¥" in repaired[1][
        "contextualized_text"
    ]
    assert repaired[1]["section_path"] == ["PHá»¤ Lá»¤C"]


def test_detect_document_profile_recognizes_admin_with_table() -> None:
    class Item:
        def __init__(self, text: str) -> None:
            self.text = text

    doc = SimpleNamespace(
        pages={1: object(), 2: object(), 3: object()},
        texts=[
            Item(
                "Cá»˜NG HÃ’A XÃƒ Há»˜I CHá»¦ NGHÄ¨A VIá»†T NAM KÃ­nh gá»­i: cÃ¡c Ä‘Æ¡n vá»‹ "
                "NÆ¡i nháº­n: nhÆ° trÃªn"
            )
        ],
        pictures=[],
        tables=[object()],
    )

    assert detect_document_profile(doc) == "administrative_with_tables"


def test_final_token_guard_splits_single_oversized_table_row() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import (
        RegexVietnameseTokenizer,
        enforce_token_limit,
    )

    tokenizer = RegexVietnameseTokenizer(max_tokens=350)
    long_cell = " ".join(f"token{i}" for i in range(430))
    record = {
        "contextualized_text": (
            "| STT | Há»‡ thá»‘ng | TÃ¬nh tráº¡ng |\n"
            "|---|---|---|\n"
            f"| 3 | Web/app CSKH | {long_cell} |"
        ),
        "text": long_cell,
        "raw_text": long_cell,
        "chunk_type": "table_rows",
    }

    parts = enforce_token_limit([record], tokenizer, 350)

    assert len(parts) >= 2
    assert all(
        tokenizer.count_tokens(part["contextualized_text"]) <= 350
        for part in parts
    )


def test_hard_token_split_prefers_sentence_boundary() -> None:
    hard_split_record_by_tokens = _docling_v6.hard_split_record_by_tokens
    build_quality_report = _docling_v6.build_quality_report
    tokenizer = RegexVietnameseTokenizer(max_tokens=80)
    sentences = [
        f"ÄÃ¢y lÃ  cÃ¢u kiá»ƒm thá»­ sá»‘ {index} cÃ³ Ä‘á»§ ná»™i dung Ä‘á»ƒ táº¡o má»™t Ä‘oáº¡n vÄƒn tá»± nhiÃªn."
        for index in range(1, 25)
    ]
    text = " ".join(sentences)
    record = {
        "chunk_id": "raw_001",
        "contextualized_text": text,
        "text": text,
        "pages": [1],
        "headings": [],
    }

    parts = hard_split_record_by_tokens(record, tokenizer, max_tokens=80)
    indexed = reindex_records(parts, tokenizer)
    quality = build_quality_report(indexed, tokenizer, max_tokens=80)

    assert len(parts) > 1
    assert all(tokenizer.count_tokens(part["contextualized_text"]) <= 80 for part in parts)
    assert not any(
        issue["issue"] == "unmerged_cross_chunk_sentence"
        for issue in quality["critical"]
    )


def test_administrative_document_with_tables_is_not_classified_as_presentation() -> None:
    """Short official letters with appendix tables must use table-aware chunking."""

    from types import SimpleNamespace

    from app.services.chunkers.chunker_docling_v6_chunking import detect_document_profile

    def item(text: str, label: str = "text") -> SimpleNamespace:
        return SimpleNamespace(text=text, label=SimpleNamespace(value=label), prov=[])

    doc = SimpleNamespace(
        pages={1: object(), 2: object(), 3: object(), 4: object()},
        texts=[
            item("Cá»˜NG HÃ’A XÃƒ Há»˜I CHá»¦ NGHÄ¨A VIá»†T NAM", "title"),
            item("Tá»”NG CÃ”NG TY ÄIá»†N Lá»°C MIá»€N TRUNG"),
            item("KÃ­nh gá»­i: CÃ´ng ty CNTT Äiá»‡n lá»±c miá»n Trung"),
            item("NÆ¡i nháº­n: NhÆ° trÃªn"),
            item("KT. Tá»”NG GIÃM Äá»C"),
            item("PHá»¤ Lá»¤C TÃŒNH TRáº NG, NGUYÃŠN NHÃ‚N VÃ€ YÃŠU Cáº¦U THá»°C HIá»†N"),
        ],
        tables=[object()],
        pictures=[object(), object()],
    )

    assert detect_document_profile(doc) == "administrative_with_tables"


def test_adaptive_admin_incident_table_becomes_key_value_records() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import (
        semanticize_administrative_tables,
    )

    record = {
        "contextualized_text": (
            "PHá»¤ Lá»¤C\n"
            "| STT | Há»‡ thá»‘ng | TÃ¬nh tráº¡ng | NguyÃªn nhÃ¢n | ÄÃ£ xá»­ lÃ½ | YÃªu cáº§u thá»±c hiá»‡n |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | CMIS | Lá»—i ghÃ©p HHC | KhÃ´ng cÃ³ dá»¯ liá»‡u | CPCIT xá»­ lÃ½ | CPCIT phá»‘i há»£p EVNICT |\n"
            "| 2 | Äo xa | Thiáº¿u Pmax | HES chÆ°a Æ°u tiÃªn | CPCIT cáº­p nháº­t | EMEC tá»‘i Æ°u thuáº­t toÃ¡n |"
        ),
        "text": "",
        "raw_text": "",
        "chunk_type": "table_rows",
        "pages": [3],
        "section_path": ["PHá»¤ Lá»¤C"],
        "headings": ["PHá»¤ Lá»¤C"],
    }

    result = semanticize_administrative_tables([record])

    assert len(result) == 2
    assert all(item["chunk_type"] == "administrative_incident" for item in result)
    assert all(item["content_format"] == "semantic_key_value" for item in result)
    assert result[0]["fields"]["Há»‡ thá»‘ng"] == "CMIS"
    assert "TÃ¬nh tráº¡ng: Lá»—i ghÃ©p HHC" in result[0]["contextualized_text"]
    assert result[1]["incident_type"] == "pmax_collection"


def test_adaptive_admin_does_not_convert_unrelated_table() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import semanticize_administrative_tables

    record = {
        "contextualized_text": (
            "| TÃªn trÆ°á»ng | Kiá»ƒu dá»¯ liá»‡u | MÃ´ táº£ |\n"
            "|---|---|---|\n"
            "| id | UUID | KhÃ³a chÃ­nh |"
        ),
        "chunk_type": "table_rows",
    }

    result = semanticize_administrative_tables([record])

    assert result == [record]


def test_adaptive_admin_body_uses_semantic_sections_not_docling_fragments() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import (
        RegexVietnameseTokenizer,
        semanticize_administrative_body,
    )

    records = [
        {
            "contextualized_text": (
                "V/v nÃ¢ng cao váº­n hÃ nh CSKH.\nKÃ­nh gá»­i: CPCIT; CPCCC; EMEC.\n"
                "Qua theo dÃµi, Tá»•ng cÃ´ng ty nháº­n tháº¥y cÃ¡c vÆ°á»›ng máº¯c sau:\n"
                "i) CMIS: Lá»—i ghÃ©p HHC.\n"
                "2. ii) Há»‡ thá»‘ng Ä‘o xa: HES chÆ°a thu tháº­p Ä‘á»§ Pmax Ä‘á»‘i vá»›i cÃ¡c cÃ´ng"
            ),
            "text": "",
            "raw_text": "",
            "chunk_type": "docling_hybrid_repaired",
            "pages": [1],
            "section_path": [],
            "headings": [],
        },
        {
            "contextualized_text": (
                "4. tÆ¡ nhiá»u biá»ƒu giÃ¡.\n"
                "iii) Web vÃ  app CSKH: KhÃ´ng hiá»ƒn thá»‹ hÃ³a Ä‘Æ¡n.\n"
                "Tá»•ng cÃ´ng ty Ä‘Ã£ há»p rÃ  soÃ¡t vÃ  yÃªu cáº§u phá»‘i há»£p kháº¯c phá»¥c.\n"
                "Trong trÆ°á»ng há»£p phÃ¡t hiá»‡n sá»± cá»‘, bÃ¡o cÃ¡o qua Ban VTCNTT."
            ),
            "text": "",
            "raw_text": "",
            "chunk_type": "docling_hybrid_repaired",
            "pages": [1],
            "section_path": ["iii) Web vÃ  app CSKH"],
            "headings": ["iii) Web vÃ  app CSKH"],
        },
    ]

    result = semanticize_administrative_body(
        records,
        tokenizer=RegexVietnameseTokenizer(max_tokens=350),
        max_tokens=350,
    )

    types = [item["chunk_type"] for item in result]
    assert types == [
        "administrative_introduction",
        "administrative_issue_overview",
        "administrative_directive",
    ]
    overview = result[1]["contextualized_text"]
    assert "2. ii)" not in overview
    assert "4. tÆ¡" not in overview
    assert "cÃ´ng\ntÆ¡" not in overview
    assert "cÃ´ng tÆ¡ nhiá»u biá»ƒu giÃ¡" in overview
    assert result[2]["section_path"] == ["Chá»‰ Ä‘áº¡o vÃ  Ä‘áº§u má»‘i bÃ¡o cÃ¡o"]


def test_adaptive_admin_does_not_inject_synthetic_labels_into_body_text() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import (
        RegexVietnameseTokenizer,
        semanticize_administrative_body,
    )

    records = [
        {
            "contextualized_text": (
                "V/v nÃ¢ng cao cÃ´ng tÃ¡c váº­n hÃ nh CSKH.\n"
                "KÃ­nh gá»­i: CPCIT; CPCCC; EMEC.\n"
                "Qua theo dÃµi, Tá»•ng cÃ´ng ty nháº­n tháº¥y cÃ¡c vÆ°á»›ng máº¯c sau:\n"
                "i) CMIS: Lá»—i ghÃ©p HHC.\n"
                "ii) Há»‡ thá»‘ng Ä‘o xa: Thiáº¿u Pmax.\n"
                "iii) Web vÃ  app CSKH: KhÃ´ng hiá»ƒn thá»‹ hÃ³a Ä‘Æ¡n.\n"
                "Tá»•ng cÃ´ng ty Ä‘Ã£ há»p rÃ  soÃ¡t vÃ  yÃªu cáº§u phá»‘i há»£p kháº¯c phá»¥c."
            ),
            "text": "",
            "raw_text": "",
            "chunk_type": "docling_hybrid_repaired",
            "pages": [1],
            "section_path": [],
            "headings": [],
        }
    ]

    result = semanticize_administrative_body(
        records,
        tokenizer=RegexVietnameseTokenizer(max_tokens=350),
        max_tokens=350,
    )

    combined = "\n".join(item["contextualized_text"] for item in result)
    assert "Loáº¡i: CÃ´ng vÄƒn hÃ nh chÃ­nh" not in combined
    assert "Chá»§ Ä‘á»:" not in combined
    assert "Má»¥c: TÃ¬nh tráº¡ng vÆ°á»›ng máº¯c" not in combined
    assert "Má»¥c: Chá»‰ Ä‘áº¡o vÃ  Ä‘áº§u má»‘i bÃ¡o cÃ¡o" not in combined
    assert result[0]["document_type"] == "administrative_document"
    assert result[0]["document_subject"] == "nÃ¢ng cao cÃ´ng tÃ¡c váº­n hÃ nh CSKH"


def test_adaptive_admin_incident_metadata_is_consistent_and_source_safe() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import semanticize_administrative_tables

    record = {
        "contextualized_text": (
            "| STT | Há»‡ thá»‘ng | TÃ¬nh tráº¡ng | NguyÃªn nhÃ¢n | ÄÃ£ xá»­ lÃ½ | YÃªu cáº§u thá»±c hiá»‡n |\n"
            "|---|---|---|---|---|---|\n"
            "| 2 | Äo xa | Thiáº¿u Pmax | HES chÆ°a Æ°u tiÃªn | CPCIT cáº­p nháº­t | EMEC tá»‘i Æ°u |\n"
            "| 2 | Äo xa | Äá»“ng bá»™ lá»—i | CÃ´ng cá»¥ lá»—i | EMEC xá»­ lÃ½ | CPCIT chá»§ trÃ¬, EMEC phá»‘i há»£p |"
        ),
        "chunk_type": "table_rows",
        "pages": [3],
    }

    result = semanticize_administrative_tables([record])

    assert [item["incident_id"] for item in result] == ["2a", "2b"]
    assert all(item["unit"] == "Äo xa" for item in result)
    assert all("Nguá»“n:" not in item["contextualized_text"] for item in result)
    assert result[1]["lead_units"] == ["CPCIT"]
    assert result[1]["coordination_units"] == ["EMEC"]
    assert result[0]["raw_text"].startswith("| 2 | Äo xa |")
