# ruff: noqa: E501

from uuid import UUID

from app.schemas.documents import VectorSearchResult
from app.services.hybrid_search import HybridSearchService
from app.services.parsers import ParsedElement
from app.services.segment_router import (
    AdaptiveSegmentChunker,
    SegmentLevelChunkRouter,
    classify_heading_line,
)

DOCUMENT_ID = UUID("77777777-7777-7777-7777-777777777777")
FIELD_CHUNK_ID = UUID("11111111-aaaa-4000-9000-000000000001")
OBJECT_CHUNK_ID = UUID("11111111-aaaa-4000-9000-000000000002")
PROCEDURE_CHUNK_ID = UUID("11111111-aaaa-4000-9000-000000000003")
OTHER_CHUNK_ID = UUID("11111111-aaaa-4000-9000-000000000004")

GIS_MIXED_TEXT = """
TỔNG CÔNG TY ĐIỆN LỰC MIỀN TRUNG
Số:
Kế hoạch xây dựng hệ thống GIS chuẩn hóa CSDL lưới điện EVNCPC
Kính gửi: Các đơn vị thành viên
Ngày 11/8/2025, HĐTV EVNCPC đã ban hành Nghị quyết số 720/NQ-HĐTV về việc xây dựng hệ thống GIS.
Mục tiêu: xây dựng, hoàn thiện, khai thác, sử dụng hiệu quả hệ thống GIS lưới điện EVNCPC.

1. CPCIT:
1.1. GIS 110kV, GIS trung thế:
- Hiệu chỉnh PMISToGIS, hiệu chỉnh WebGIS 110kV, publish dữ liệu GIS.
- Hướng dẫn sử dụng công cụ, phối hợp sáp nhập dữ liệu, hỗ trợ KHoPC.
- Lập phương án WebGIS trung thế trong tháng 10/2025 và tháng 11/2025.
1.2. GIS hạ thế:
- Báo cáo phương án tháng 10/2025.
- Hoàn thành công cụ trước ngày 31/3/2026.
- Khởi tạo, chuyển đổi, hướng dẫn trong tháng 4/2026.
2. Các CTĐL (trừ KHoPC):
2.1. GIS 110kV, GIS trung thế:
- Kiểm tra, hiệu chỉnh CSDL offline, gửi lại CPCIT.
2.2. GIS hạ thế:
- Chuẩn bị dữ liệu, phối hợp chuyển đổi GIS hạ thế.
- DNPC, HPC hoàn thành trong tháng 6/2026; các CTĐL còn lại hoàn thành trong tháng 9/2026.
3. KHoPC:
3.1. GIS 110kV, GIS trung thế:
- Phối hợp sáp nhập dữ liệu với CPCIT.
- Đào tạo trong tháng 09/2025, hoàn thành GIS 110kV trong tháng 12/2025 và GIS trung thế trong quý 01/2026.
3.2. GIS hạ thế:
- Chuẩn hóa dữ liệu hạ thế.
4. Các đơn vị thành viên:
- Tổ chức thực hiện và báo cáo kết quả.

Nơi nhận:
- Như trên;
Lê Hoàng Anh Dũng

Phụ lục 01
PHƯƠNG ÁN SÁP NHẬP DỮ LIỆU GIS 110kV, GIS TRUNG THẾ
TT | Dữ liệu | CPCIT | Các CTĐL (trừ KHoPC)
1 | Dữ liệu GIS 110kV | Chuẩn bị, load dữ liệu, gửi CSDL offline, cập nhật GIS live | Kiểm tra, hiệu chỉnh CSDL offline, gửi lại CPCIT
2 | Dữ liệu GIS trung thế | Lập tài liệu hướng dẫn, tạo nhóm hỗ trợ, cập nhật GIS live | Thực hiện gộp dữ liệu theo hướng dẫn, kiểm tra hiệu chỉnh CSDL

Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO VÀ CHUYỂN ĐỔI GIS HẠ THẾ
Mục tiêu: khởi tạo khung CSDL GIS hạ thế gồm 10 đối tượng thiết bị; chuyển đổi dữ liệu ban đầu cho 07 đối tượng ưu tiên.
F08_CotDien_HT, F09_DuongDay_HT, F05_CongToKhachHang_HT, F01_DiemDo_HT, F03_ThietBiDoDem_HT, F02_ThietBiDongCat_HT, F10_TuPhanPhoi_HT

(1) F08_CotDien_HT – Lớp cột điện
Số lượng trường: 30
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | ID | ID Cột điện | Text | | 20 | ID tự sinh của GIS |
2 | IdPMIS | Mã search từ PMIS | Text | | 25 | Biên tập |
3 | MaTramBienAp | Mã trạm biến áp | Text | | 50 | TTHT | X
4 | ViTriCotHaThe | Vị trí cột hạ thế | Text | | 100 | TTHT | X
5 | KieuCot | Kiểu cột | Text | | 50 | TTHT | X
6 | LoaiCot | Loại cột | Text | | 50 | PMIS | X
7 | ChieuCaoCot | Chiều cao cột | Number | | 10 | PMIS | X
8 | DonViQuanLy | Đơn vị quản lý | Text | | 50 | TTHT | X
9 | X | Tọa độ X | Number | | 20 | Biên tập | X
10 | Y | Tọa độ Y | Number | | 20 | Biên tập | X
11 | Long | Kinh độ | Number | | 20 | Biên tập | X
12 | Lat | Vĩ độ | Number | | 20 | TTHT | X
13 | MaPMIS | Mã PMIS | Text | | 50 | Biên tập |

(2) F09_DuongDay_HT – Lớp đường dây
TT | Tên trường | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | ID | ID đường dây | Text | | 20 | ID tự sinh của GIS |
2 | DienAp | Điện áp | Text | | 20 | PMIS | X

(3) F05_CongToKhachHang_HT – Lớp công tơ khách hàng
Số lượng trường: 5
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | MaCongTo | Mã công tơ | Text | | 50 | CMIS | X
2 | MaKhachHang | Mã khách hàng | Text | | 50 | CMIS | X
3 | TenKhachHang | Tên khách hàng | Text | | 255 | CMIS | X
4 | DiaChi | Địa chỉ | Text | | 255 | CMIS | X
5 | MaCMIS | Mã CMIS | Text | | 50 | CMIS | X

(4) F01_DiemDo_HT – Lớp điểm đo
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | MaTramBienAp | Mã trạm biến áp | Text | | 50 | TTHT | X

(5) F03_ThietBiDoDem_HT – Lớp thiết bị đo đếm
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | MaThietBi | Mã thiết bị | Text | | 50 | CMIS | X

(6) F02_ThietBiDongCat_HT – Lớp thiết bị đóng cắt
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | MaThietBiDongCat | Mã thiết bị đóng cắt | Text | | 50 | PMIS | X

(7) F10_TuPhanPhoi_HT – Lớp tủ phân phối
TT | Trường dữ liệu | Mô tả | Kiểu dữ liệu | Miền giá trị | Độ rộng | Nguồn dữ liệu | Chuyển đổi sang GIS
1 | MaTu | Mã tủ | Text | | 50 | TTHT | X

3. Khởi tạo bổ sung 03 bảng dữ liệu thuộc tính
(1) HinhAnhCotDien – Hình ảnh cột điện
Tên bảng dữ liệu: HinhAnhCotDien
Mô tả đường dẫn hình ảnh cột điện
TT Tên trường Mô tả Kiểu dữ liệu Miền giá trị Độ rộng
1 IDHinhAnh ID hình ảnh Text 50
2 IDCotDien ID cột điện Text 50
(2) HinhAnhKhachHang – Hình ảnh khách hàng
Tên bảng dữ liệu: HinhAnhKhachHang
Mô tả đường dẫn hình ảnh khách hàng
TT Tên trường Mô tả Kiểu dữ liệu Miền giá trị Độ rộng
1 IDHinhAnh ID hình ảnh Text 50
2 IDCongToKhachHang ID công tơ khách hàng Text 50
(3) HinhAnhHoSoKhachHang – Hình ảnh hồ sơ khách hàng
Tên bảng dữ liệu: HinhAnhHoSoKhachHang
Mô tả đường dẫn hình ảnh hồ sơ khách hàng
TT Tên trường Mô tả Kiểu dữ liệu Miền giá trị Độ rộng
1 IDHinhAnh ID hình ảnh Text 50
2 IDCongToKhachHang ID công tơ khách hàng Text 50

4. Khởi tạo bổ sung 03 mối quan hệ giữa lớp dữ liệu GIS với bảng dữ liệu thuộc tính
Tên mối quan hệ PXXXXX_CotDien_HT_HinhAnhCotDien Ghi chú
Lớp dữ liệu: F08_PXXXXX_CotDien_HT
Trường thuộc tính liên kết của lớp dữ liệu: F08_PXXXXX_CotDien_HT ID
Bảng dữ liệu: PX_HinhAnhCotDien
Trường thuộc tính liên kết của lớp dữ liệu: PX_HinhAnhCotDien IDCotDien
Mối quan hệ: lớp dữ liệu Cột điện hạ thế với bảng dữ liệu hình ảnh cột điện 1 - Nhiều (1-M)
Tên mối quan hệ PXXXXX_CongToKhachHang_HT_HinhAnhKhachHang Ghi chú
Lớp dữ liệu: F05_PXXXXX_CongToKhachHang_HT
Bảng dữ liệu: PX_HinhAnhKhachHang
Trường thuộc tính liên kết PX_HinhAnhKhachHang IDCongToKhachHang
Mối quan hệ: lớp dữ liệu Công tơ khách hàng hạ thế với bảng dữ liệu hình ảnh khách hàng 1 - Nhiều (1-M)
Tên mối quan hệ PXXXXX_CongToKhachHang_HT_HinhAnhHoSoKhachHang Ghi chú
Lớp dữ liệu: F05_PXXXXX_CongToKhachHang_HT
Bảng dữ liệu: PX_HinhAnhHoSoKhachHang
Trường thuộc tính liên kết PX_HinhAnhHoSoKhachHang IDCongToKhachHang
Mối quan hệ: lớp dữ liệu Công tơ khách hàng hạ thế với bảng dữ liệu hình ảnh hồ sơ khách hàng 1 - Nhiều (1-M)
"""

PAGE_ELEMENTS = [
    ParsedElement(element_type="page", text=GIS_MIXED_TEXT, page_number=1),
]


def _chunks():
    return AdaptiveSegmentChunker().chunk_text(GIS_MIXED_TEXT, parsed_elements=PAGE_ELEMENTS)


def test_segment_router_mixed_gis_document() -> None:
    plan = SegmentLevelChunkRouter().plan(
        parsed_text=GIS_MIXED_TEXT,
        parsed_elements=PAGE_ELEMENTS,
    )

    assert plan.document_profile == "mixed_administrative_technical"
    assert plan.chunk_strategy == "adaptive_segmented"
    segment_types = {segment.segment_type for segment in plan.segments}
    assert {
        "administrative_dispatch",
        "assignment_section",
        "procedure_table",
        "schema_appendix",
        "schema_table",
        "footer",
    } <= segment_types


def test_heading_detector_does_not_promote_admin_fragments() -> None:
    rejected = [
        "Số:",
        "Ngày 11/8/2025, HĐTV EVNCPC đã ban hành Nghị quyết số 720/NQ-HĐTV",
        "HĐTV về việc xây dựng hệ thống GIS chuẩn hóa CSDL lưới điện",
        "Lê Hoàng Anh Dũng",
        "1 ID ID Cột điện Text 20 ID tự sinh của GIS",
    ]

    assert all(not classify_heading_line(line).is_heading for line in rejected)
    assert classify_heading_line("1.1. GIS 110kV, GIS trung thế:").is_heading
    assert classify_heading_line("Phụ lục 02").is_heading
    assert classify_heading_line("(1) F08_CotDien_HT – Lớp cột điện").is_heading


def test_admin_dispatch_chunks_are_grouped_by_unit_and_subsection() -> None:
    chunks = _chunks()
    assignments = [
        chunk for chunk in chunks if chunk["metadata"]["chunk_type"] == "assignment_section"
    ]
    by_section = {chunk["metadata"]["section_id"]: chunk["metadata"] for chunk in assignments}

    assert by_section["1.1"]["unit"] == "CPCIT"
    assert by_section["1.1"]["scope"] == "GIS 110kV, GIS trung thế"
    assert by_section["1.2"]["unit"] == "CPCIT"
    assert by_section["2.1"]["unit"] == "Các CTĐL (trừ KHoPC)"
    assert by_section["2.2"]["unit"] == "Các CTĐL (trừ KHoPC)"
    assert by_section["3.1"]["unit"] == "KHoPC"
    assert by_section["3.2"]["unit"] == "KHoPC"
    assert by_section["4"]["unit"] == "Các đơn vị thành viên"
    assert any(chunk["metadata"]["chunk_type"] == "administrative_footer" for chunk in chunks)


def test_deadline_index_chunk_contains_key_milestones() -> None:
    deadline = next(chunk for chunk in _chunks() if chunk["metadata"]["chunk_type"] == "deadline_index")

    for value in [
        "tháng 10/2025",
        "trước ngày 31/3/2026",
        "tháng 4/2026",
        "tháng 6/2026",
        "tháng 9/2026",
    ]:
        assert value in deadline["content"]
    assert deadline["metadata"]["retrieval_priority"] == "high"


def test_appendix_01_procedure_table_rows() -> None:
    rows = [
        chunk for chunk in _chunks() if chunk["metadata"]["chunk_type"] == "procedure_table_row"
    ]

    assert [row["metadata"]["data_type"] for row in rows] == [
        "GIS 110kV",
        "GIS trung thế",
    ]
    assert all(row["metadata"]["appendix_id"] == "01" for row in rows)
    assert all(
        row["metadata"]["responsible_columns"] == ["CPCIT", "Các CTĐL (trừ KHoPC)"] for row in rows
    )
    assert "cập nhật GIS live" in rows[0]["metadata"]["cpcit"]
    assert "hiệu chỉnh CSDL offline" in rows[0]["metadata"]["ctdl"]


def test_procedure_rows_prefer_parsed_table_elements_without_pipe_text() -> None:
    text = """
GIS EVNCPC
Phụ lục 01
PHƯƠNG ÁN SÁP NHẬP DỮ LIỆU GIS 110kV, GIS TRUNG THẾ
Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO VÀ CHUYỂN ĐỔI GIS HẠ THẾ
(1) F08_CotDien_HT – Lớp cột điện
TT Trường dữ liệu Mô tả Kiểu dữ liệu Miền giá trị Độ rộng Nguồn dữ liệu Chuyển đổi sang GIS
1 ID ID Cột điện Text 20 ID tự sinh của GIS
"""
    elements = [
        ParsedElement(element_type="page", text=text, page_number=1),
        ParsedElement(
            element_type="table_row",
            text="GIS 110kV procedure row",
            page_number=1,
            table_id="procedure_table",
            row_index=1,
            metadata={
                "headers": ["TT", "Dữ liệu", "CPCIT", "Các CTĐL (trừ KHoPC)"],
                "values": [
                    "1",
                    "GIS 110kV",
                    "CPCIT load dữ liệu",
                    "CTĐL kiểm tra offline",
                ],
            },
        ),
    ]

    rows = [
        chunk
        for chunk in AdaptiveSegmentChunker().chunk_text(text, parsed_elements=elements)
        if chunk["metadata"]["chunk_type"] == "procedure_table_row"
    ]

    assert len(rows) == 1
    assert rows[0]["metadata"]["data_type"] == "GIS 110kV"
    assert rows[0]["metadata"]["cpcit"] == "CPCIT load dữ liệu"
    assert rows[0]["metadata"]["ctdl"] == "CTĐL kiểm tra offline"


def test_appendix_02_schema_intro() -> None:
    overview = next(
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "schema_appendix_overview"
    )

    assert overview["metadata"]["appendix_id"] == "02"
    assert overview["metadata"]["object_codes"] == [
        "F08_CotDien_HT",
        "F09_DuongDay_HT",
        "F05_CongToKhachHang_HT",
        "F01_DiemDo_HT",
        "F03_ThietBiDoDem_HT",
        "F02_ThietBiDongCat_HT",
        "F10_TuPhanPhoi_HT",
    ]
    assert overview["metadata"]["priority_object_count"] == 7
    assert set(overview["metadata"]["schema_object_codes"]) >= set(
        overview["metadata"]["object_codes"]
    )
    assert "F10_TuPhanPhoi_HT" in overview["content"]


def test_schema_table_f08_field_rows() -> None:
    rows = [
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "schema_field_row"
        and chunk["metadata"]["object_code"] == "F08_CotDien_HT"
    ]
    field_names = [row["metadata"]["field_name"] for row in rows]

    assert {
        "ID",
        "IdPMIS",
        "MaTramBienAp",
        "ViTriCotHaThe",
        "KieuCot",
        "DonViQuanLy",
        "X",
        "Y",
        "Long",
        "Lat",
    } <= set(field_names)
    ma_tram = next(row for row in rows if row["metadata"]["field_name"] == "MaTramBienAp")
    assert ma_tram["metadata"]["data_type"] == "Text"
    assert ma_tram["metadata"]["width"] == "50"
    assert ma_tram["metadata"]["source_data"] == "TTHT"
    assert ma_tram["metadata"]["convert_to_gis"] is True
    by_name = {row["metadata"]["field_name"]: row["metadata"] for row in rows}
    for field_name in ["ViTriCotHaThe", "KieuCot", "DonViQuanLy", "Lat"]:
        assert by_name[field_name]["source_data"] == "TTHT"
        assert by_name[field_name]["convert_to_gis"] is True
    assert by_name["MaPMIS"]["source_data"] == "Biên tập"
    assert by_name["MaPMIS"]["convert_to_gis"] is False


def test_schema_object_summary_f08() -> None:
    summary = next(
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "schema_object_summary"
        and chunk["metadata"]["object_code"] == "F08_CotDien_HT"
    )

    assert summary["metadata"]["object_name"] == "Lớp cột điện"
    assert summary["metadata"]["field_count"] == 30
    assert set(
        [
            "MaTramBienAp",
            "ViTriCotHaThe",
            "KieuCot",
            "LoaiCot",
            "ChieuCaoCot",
            "DonViQuanLy",
            "X",
            "Y",
            "Long",
            "Lat",
        ]
    ) <= set(summary["metadata"]["converted_fields"])
    assert set(summary["metadata"]["source_systems"]) >= {"TTHT", "PMIS", "Biên tập"}


def test_schema_object_summary_for_all_priority_objects() -> None:
    summaries = [
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "schema_object_summary"
    ]
    object_codes = {chunk["metadata"]["object_code"] for chunk in summaries}

    assert {
        "F08_CotDien_HT",
        "F09_DuongDay_HT",
        "F05_CongToKhachHang_HT",
        "F01_DiemDo_HT",
        "F03_ThietBiDoDem_HT",
        "F02_ThietBiDongCat_HT",
        "F10_TuPhanPhoi_HT",
    } <= object_codes


def test_schema_table_f05_cmis_field_rows() -> None:
    rows = [
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "schema_field_row"
        and chunk["metadata"]["object_code"] == "F05_CongToKhachHang_HT"
    ]
    by_name = {row["metadata"]["field_name"]: row["metadata"] for row in rows}

    for field_name in ["MaCongTo", "MaKhachHang", "TenKhachHang", "DiaChi", "MaCMIS"]:
        assert by_name[field_name]["source_data"] == "CMIS"
        assert by_name[field_name]["convert_to_gis"] is True


def test_relationship_schema_chunks() -> None:
    relationships = [
        chunk
        for chunk in _chunks()
        if chunk["metadata"]["chunk_type"] == "gis_relationship_schema"
    ]
    by_name = {chunk["metadata"]["relationship_name"]: chunk["metadata"] for chunk in relationships}

    cot = by_name["PXXXXX_CotDien_HT_HinhAnhCotDien"]
    assert cot["source_layer"] == "F08_PXXXXX_CotDien_HT"
    assert cot["source_key"] == "ID"
    assert cot["target_table"] == "PX_HinhAnhCotDien"
    assert cot["target_key"] == "IDCotDien"
    assert cot["cardinality"] == "1-M"
    assert by_name["PXXXXX_CongToKhachHang_HT_HinhAnhKhachHang"]["target_key"] == "IDCongToKhachHang"
    assert by_name["PXXXXX_CongToKhachHang_HT_HinhAnhHoSoKhachHang"]["target_key"] == "IDCongToKhachHang"


def test_no_schema_rows_as_heading_section() -> None:
    bad_titles = {
        "1 ID ID Cột điện Text 20 ID tự sinh của GIS",
        "2 IdPMIS Mã search từ PMIS Text 25 Biên tập",
        "3 MaTramBienAp Mã trạm biến áp Text 50 TTHT X",
    }

    assert not any(
        chunk["metadata"].get("chunk_type") == "heading_section"
        and chunk["metadata"].get("section_title") in bad_titles
        for chunk in _chunks()
    )


def test_retrieval_exact_schema_field() -> None:
    results = HybridSearchService.fuse_results(
        query="Trường MaTramBienAp của F08_CotDien_HT lấy từ nguồn dữ liệu nào?",
        vector_results=[
            VectorSearchResult(
                chunk_id=OTHER_CHUNK_ID,
                document_id=DOCUMENT_ID,
                score=0.99,
                content_preview="F08_CotDien_HT schema summary",
                metadata={"chunk_type": "schema_object_summary", "object_code": "F08_CotDien_HT"},
            ),
            VectorSearchResult(
                chunk_id=FIELD_CHUNK_ID,
                document_id=DOCUMENT_ID,
                score=0.2,
                content_preview="MaTramBienAp Mã trạm biến áp TTHT",
                metadata={
                    "chunk_type": "schema_field_row",
                    "object_code": "F08_CotDien_HT",
                    "field_name": "MaTramBienAp",
                    "source_data": "TTHT",
                },
            ),
        ],
        keyword_results=[],
        top_k=2,
    )

    assert results[0].chunk_id == FIELD_CHUNK_ID
    assert results[0].metadata["chunk_type"] == "schema_field_row"
    assert results[0].metadata["metadata_exact_boost"] >= 20


def test_retrieval_procedure_table() -> None:
    results = HybridSearchService.fuse_results(
        query="Phụ lục 01 CPCIT phải làm gì với GIS 110kV?",
        vector_results=[
            VectorSearchResult(
                chunk_id=OBJECT_CHUNK_ID,
                document_id=DOCUMENT_ID,
                score=0.95,
                content_preview="schema object unrelated",
                metadata={"chunk_type": "schema_object_summary", "object_code": "F08_CotDien_HT"},
            ),
            VectorSearchResult(
                chunk_id=PROCEDURE_CHUNK_ID,
                document_id=DOCUMENT_ID,
                score=0.2,
                content_preview="CPCIT chuẩn bị load dữ liệu GIS 110kV",
                metadata={
                    "chunk_type": "procedure_table_row",
                    "appendix_id": "01",
                    "data_type": "Dữ liệu GIS 110kV",
                },
            ),
        ],
        keyword_results=[],
        top_k=2,
    )

    assert results[0].chunk_id == PROCEDURE_CHUNK_ID
    assert results[0].metadata["chunk_type"] == "procedure_table_row"
