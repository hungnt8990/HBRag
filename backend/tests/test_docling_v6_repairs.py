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
    text = """3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính
(7) F10_TuPhanPhoi_HT - Lớp tủ phân phối
(1) HinhAnhCotDien - Hình ảnh cột điện
Tên bảng dữ liệu: HinhAnhCotDien
Mô tả đường dẫn hình ảnh cột điện
| TT | Tên trường | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng |
|---|---|---|---|---|---|
| 1 | IDHinhAnh | ID hình ảnh | Text | | 50 |
| 2 | IDCotDien | ID cột điện | Text | | 50 |"""
    records = repair_table_identity(
        [
            {
                "contextualized_text": text,
                "text": text,
                "table_name": "F10_TuPhanPhoi_HT",
                "section_path": [
                    "3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính",
                    "(7) F10_TuPhanPhoi_HT - Lớp tủ phân phối",
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
    f10_text = """(7) F10_TuPhanPhoi_HT - Lớp tủ phân phối
| TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Nguồn dữ liệu |
|---|---|---|---|---|
| 1 | ID | ID tủ hạ thế | Text | ID tự sinh của GIS |"""
    attribute_text = """3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính
(7) F10_TuPhanPhoi_HT - Lớp tủ phân phối
(1) HinhAnhCotDien - Hình ảnh cột điện
Tên bảng dữ liệu: HinhAnhCotDien
Mô tả đường dẫn hình ảnh cột điện
| TT | Tên trường | Mô tả | Kiểu dữ liệu | Độ rộng |
|---|---|---|---|---|
| 1 | IDHinhAnh | ID hình ảnh | Text | 50 |
| 2 | IDCotDien | ID cột điện | Text | 50 |"""
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
        "contextualized_text": "1.2. GIS hạ thế:\nNội dung đã sửa.",
        "text": "old",
        "raw_text": "một block nguồn dài chứa cả mục 1.2 và 2.1",
        "pages": [1, 2],
    }

    result = synchronize_record_provenance([record])[0]

    assert result["raw_text"] == result["text"]
    assert result["normalized_text"] == result["text"]
    assert result["source_raw_text"] == "một block nguồn dài chứa cả mục 1.2 và 2.1"
    assert result["provenance_status"] == "chunk_aligned"


def test_table_repacking_avoids_a_tiny_tail() -> None:
    prefix = ["(1) F05_CongToKhachHang_HT - Lớp công tơ khách hàng"]
    header = [
        "| TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Nguồn dữ liệu |",
        "|---|---|---|---|---|",
    ]
    rows = [
        f"| {index} | Field{index} | Mô tả trường dữ liệu số {index} có nội dung | Text | CMIS |"
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
            "table_description": "Lớp cột điện",
            "chunk_type": "table_rows",
            "content_format": "markdown_table",
            "field_names": ["ID", "MaTramBienAp"],
            "source_systems": ["GIS", "TTHT"],
            "convertible_fields": ["MaTramBienAp"],
            "row_start": 1,
            "row_end": 2,
            "pages": [5],
            "section_path": ["F08_CotDien_HT - Lớp cột điện"],
            "headings": ["F08_CotDien_HT - Lớp cột điện"],
        },
        {
            "contextualized_text": "F08 part 2",
            "text": "F08 part 2",
            "raw_text": "F08 part 2",
            "table_name": "F08_CotDien_HT",
            "table_description": "Lớp cột điện",
            "chunk_type": "table_rows",
            "content_format": "markdown_table",
            "field_names": ["X", "Y"],
            "source_systems": ["TTHT"],
            "convertible_fields": ["X", "Y"],
            "row_start": 3,
            "row_end": 4,
            "pages": [6],
            "section_path": ["F08_CotDien_HT - Lớp cột điện"],
            "headings": ["F08_CotDien_HT - Lớp cột điện"],
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
            "contextualized_text": "2.2. GIS hạ thế:\nHoàn thành trong tháng 9/2026.",
            "section_path": ["2. Các CTĐL", "2.2. GIS hạ thế"],
        },
        {
            "contextualized_text": "3.2. GIS hạ thế:\nTriển khai theo kế hoạch tại mục 2.2.",
            "section_path": ["3. KHoPC", "3.2. GIS hạ thế"],
        },
    ]

    resolved = resolve_cross_references(records)

    assert resolved[1]["cross_references"] == ["2.2"]
    assert "tháng 9/2026" in resolved[1]["resolved_reference_text"]


def test_semantic_validator_rejects_table_metadata_mismatch() -> None:
    text = """Tên bảng dữ liệu: HinhAnhCotDien
| TT | Tên trường |
|---|---|
| 1 | IDHinhAnh |"""
    issues = semantic_validation_issues(
        {
            "contextualized_text": text,
            "raw_text": text,
            "table_name": "F10_TuPhanPhoi_HT",
            "chunk_type": "table_rows",
            "section_path": ["(7) F10_TuPhanPhoi_HT - Lớp tủ phân phối"],
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
        "Tên bảng dữ liệu: HinhAnhCotDien\n"
        "| TT | Tên trường |\n"
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
        "section_path": ["3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính"],
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
        "section_path": ["3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính"],
        "table_name": "F10_TuPhanPhoi_HT",
        "entity": "F10_TuPhanPhoi_HT",
    }
    first_text = (
        "Tên bảng dữ liệu: HinhAnhCotDien\n"
        "| TT | Tên trường |\n"
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
        "Tên bảng dữ liệu: HinhAnhCotDien\n"
        "| TT | Tên trường |\n"
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
    raw = "CÔNG C Ụ ĐÔ NG B Ộ D Ữ LI Ệ U GIS H Ạ THÊ"
    assert normalize_vietnamese_pdf_text(raw) == (
        "CÔNG CỤ ĐỒNG BỘ DỮ LIỆU GIS HẠ THẾ"
    )


def test_normalize_vietnamese_pdf_text_keeps_real_word_boundaries() -> None:
    raw = "Căn c ứ vb s ố 6515 và L ớ p thi ế t b ị đóng c ắ t"
    assert normalize_vietnamese_pdf_text(raw) == (
        "Căn cứ vb số 6515 và Lớp thiết bị đóng cắt"
    )


def test_strip_image_placeholders_removes_visual_only_markers() -> None:
    assert strip_image_placeholders(
        "GIỚI THIỆU CÔNG CỤ\n<!-- image -->\n<!-- image -->"
    ) == "GIỚI THIỆU CÔNG CỤ"


def test_normalize_vietnamese_pdf_text_keeps_space_before_accented_word() -> None:
    assert normalize_vietnamese_pdf_text("đã có ở các phần mềm") == (
        "đã có ở các phần mềm"
    )
    assert normalize_vietnamese_pdf_text("chuyển đổi ở giai đoạn sau") == (
        "chuyển đổi ở giai đoạn sau"
    )


def test_cross_page_table_carries_merged_leading_cells() -> None:
    first = {
        "contextualized_text": (
            "PHỤ LỤC\n"
            "| STT | Hệ thống | Tình trạng | Nguyên nhân | "
            "Đã xử lý | Yêu cầu thực hiện |\n"
            "|---|---|---|---|---|---|\n"
            "| 3 | Web/app CSKH | Hiển thị sai | Nguồn dữ liệu sai | Đã đồng bộ lại | Lấy từ CMIS |"
        ),
        "text": "",
        "pages": [3],
        "section_path": ["PHỤ LỤC"],
    }
    second = {
        "contextualized_text": (
            "| STT | Hệ thống | Tình trạng | Nguyên nhân | "
            "Đã xử lý | Yêu cầu thực hiện |\n"
            "|---|---|---|---|---|---|\n"
            "|  |  | Không hiển thị hóa đơn | Dùng chỉ số chốt cũ | "
            "Đã đồng bộ lại | Lấy hóa đơn từ CMIS |\n"
            "|  |  | Gián đoạn dịch vụ | Thiếu HA DC-DR | Đã khôi phục | Hoàn thiện HA |"
        ),
        "text": "",
        "pages": [4],
        "section_path": [],
    }

    repaired = repair_cross_page_table_continuations([first, second])

    assert repaired[1]["cross_page_table_continuation"] is True
    assert "| 3 | Web/app CSKH | Không hiển thị hóa đơn" in repaired[1][
        "contextualized_text"
    ]
    assert "| 3 | Web/app CSKH | Gián đoạn dịch vụ" in repaired[1][
        "contextualized_text"
    ]
    assert repaired[1]["section_path"] == ["PHỤ LỤC"]


def test_detect_document_profile_recognizes_admin_with_table() -> None:
    class Item:
        def __init__(self, text: str) -> None:
            self.text = text

    doc = SimpleNamespace(
        pages={1: object(), 2: object(), 3: object()},
        texts=[
            Item(
                "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM Kính gửi: các đơn vị "
                "Nơi nhận: như trên"
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
            "| STT | Hệ thống | Tình trạng |\n"
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
        f"Đây là câu kiểm thử số {index} có đủ nội dung để tạo một đoạn văn tự nhiên."
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
            item("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "title"),
            item("TỔNG CÔNG TY ĐIỆN LỰC MIỀN TRUNG"),
            item("Kính gửi: Công ty CNTT Điện lực miền Trung"),
            item("Nơi nhận: Như trên"),
            item("KT. TỔNG GIÁM ĐỐC"),
            item("PHỤ LỤC TÌNH TRẠNG, NGUYÊN NHÂN VÀ YÊU CẦU THỰC HIỆN"),
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
            "PHỤ LỤC\n"
            "| STT | Hệ thống | Tình trạng | Nguyên nhân | Đã xử lý | Yêu cầu thực hiện |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | CMIS | Lỗi ghép HHC | Không có dữ liệu | CPCIT xử lý | CPCIT phối hợp EVNICT |\n"
            "| 2 | Đo xa | Thiếu Pmax | HES chưa ưu tiên | CPCIT cập nhật | EMEC tối ưu thuật toán |"
        ),
        "text": "",
        "raw_text": "",
        "chunk_type": "table_rows",
        "pages": [3],
        "section_path": ["PHỤ LỤC"],
        "headings": ["PHỤ LỤC"],
    }

    result = semanticize_administrative_tables([record])

    assert len(result) == 2
    assert all(item["chunk_type"] == "administrative_incident" for item in result)
    assert all(item["content_format"] == "semantic_key_value" for item in result)
    assert result[0]["fields"]["Hệ thống"] == "CMIS"
    assert "Tình trạng: Lỗi ghép HHC" in result[0]["contextualized_text"]
    assert result[1]["incident_type"] == "pmax_collection"


def test_adaptive_admin_does_not_convert_unrelated_table() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import semanticize_administrative_tables

    record = {
        "contextualized_text": (
            "| Tên trường | Kiểu dữ liệu | Mô tả |\n"
            "|---|---|---|\n"
            "| id | UUID | Khóa chính |"
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
                "V/v nâng cao vận hành CSKH.\nKính gửi: CPCIT; CPCCC; EMEC.\n"
                "Qua theo dõi, Tổng công ty nhận thấy các vướng mắc sau:\n"
                "i) CMIS: Lỗi ghép HHC.\n"
                "2. ii) Hệ thống đo xa: HES chưa thu thập đủ Pmax đối với các công"
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
                "4. tơ nhiều biểu giá.\n"
                "iii) Web và app CSKH: Không hiển thị hóa đơn.\n"
                "Tổng công ty đã họp rà soát và yêu cầu phối hợp khắc phục.\n"
                "Trong trường hợp phát hiện sự cố, báo cáo qua Ban VTCNTT."
            ),
            "text": "",
            "raw_text": "",
            "chunk_type": "docling_hybrid_repaired",
            "pages": [1],
            "section_path": ["iii) Web và app CSKH"],
            "headings": ["iii) Web và app CSKH"],
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
    assert "4. tơ" not in overview
    assert "công\ntơ" not in overview
    assert "công tơ nhiều biểu giá" in overview
    assert result[2]["section_path"] == ["Chỉ đạo và đầu mối báo cáo"]


def test_adaptive_admin_does_not_inject_synthetic_labels_into_body_text() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import (
        RegexVietnameseTokenizer,
        semanticize_administrative_body,
    )

    records = [
        {
            "contextualized_text": (
                "V/v nâng cao công tác vận hành CSKH.\n"
                "Kính gửi: CPCIT; CPCCC; EMEC.\n"
                "Qua theo dõi, Tổng công ty nhận thấy các vướng mắc sau:\n"
                "i) CMIS: Lỗi ghép HHC.\n"
                "ii) Hệ thống đo xa: Thiếu Pmax.\n"
                "iii) Web và app CSKH: Không hiển thị hóa đơn.\n"
                "Tổng công ty đã họp rà soát và yêu cầu phối hợp khắc phục."
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
    assert "Loại: Công văn hành chính" not in combined
    assert "Chủ đề:" not in combined
    assert "Mục: Tình trạng vướng mắc" not in combined
    assert "Mục: Chỉ đạo và đầu mối báo cáo" not in combined
    assert result[0]["document_type"] == "administrative_document"
    assert result[0]["document_subject"] == "nâng cao công tác vận hành CSKH"


def test_adaptive_admin_incident_metadata_is_consistent_and_source_safe() -> None:
    from app.services.chunkers.chunker_docling_v6_chunking import semanticize_administrative_tables

    record = {
        "contextualized_text": (
            "| STT | Hệ thống | Tình trạng | Nguyên nhân | Đã xử lý | Yêu cầu thực hiện |\n"
            "|---|---|---|---|---|---|\n"
            "| 2 | Đo xa | Thiếu Pmax | HES chưa ưu tiên | CPCIT cập nhật | EMEC tối ưu |\n"
            "| 2 | Đo xa | Đồng bộ lỗi | Công cụ lỗi | EMEC xử lý | CPCIT chủ trì, EMEC phối hợp |"
        ),
        "chunk_type": "table_rows",
        "pages": [3],
    }

    result = semanticize_administrative_tables([record])

    assert [item["incident_id"] for item in result] == ["2a", "2b"]
    assert all(item["unit"] == "Đo xa" for item in result)
    assert all("Nguồn:" not in item["contextualized_text"] for item in result)
    assert result[1]["lead_units"] == ["CPCIT"]
    assert result[1]["coordination_units"] == ["EMEC"]
    assert result[0]["raw_text"].startswith("| 2 | Đo xa |")
