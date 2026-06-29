from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_log_repository, get_doffice_ingestion_service
from app.main import app
from app.schemas.documents import ChunkPreview, DocumentChunkResponse, DocumentVectorIndexResponse, DofficeIngestResponse
from app.services.chunkers.chunker_doffice_chunking import (
    TABLE_CHUNK_MAX_CHARS,
    _is_section_title_only_chunk,
    _merge_tiny_last_table_piece,
    _table_chunks,
    build_doffice_chunks,
)
from app.services.document_sources import DOFFICE_SOURCE_TYPE, DofficeDocument, DofficeElasticsearchSource
from app.services.ingestion.ingestion_doffice_content_normalizer import (
    NormalizedDofficeDocument,
    NormalizedTable,
    apply_spacing_fixes,
    normalize_doffice_source,
)
from app.services.ingestion.ingestion_doffice_ingestion_service import DofficeIngestionService, DofficeIngestOptions
from app.services.retrieval.retrieval_hybrid_search import IDENTIFIER_EXACT_BOOST, identifier_exact_match_boost
from app.services.retrieval.retrieval_keyword_search import KeywordSearchService
from app.services.text_cleaning import clean_doffice_markdown_to_text

DOCUMENT_ID = UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
EXISTING_DOCUMENT_ID = UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
USER_ID = UUID("cccccccc-3333-3333-3333-cccccccccccc")
ORG_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
KB_ID = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")


def test_clean_doffice_markdown_to_text_preserves_table_content() -> None:
    raw = """
--- Page 1 ---
## 1. CPCIT:
[Image]
<table>
  <tr><th>TT</th><th>Trường dữ liệu</th><th>Nguồn dữ liệu</th><th>Chuyển đổi sang GIS</th></tr>
  <tr><td>1</td><td><strong>MaTramBienAp</strong></td><td>CPCIT</td><td>GIS 110kV</td></tr>
</table>
<br>
- - Như trên;
"""

    cleaned = clean_doffice_markdown_to_text(raw)

    assert "<table" not in cleaned
    assert "<td" not in cleaned
    assert "<tr" not in cleaned
    assert "<strong" not in cleaned
    assert "[Image]" not in cleaned
    assert "--- Page" not in cleaned
    assert "1. CPCIT:" in cleaned
    assert "MaTramBienAp" in cleaned
    assert "Chuyển đổi sang GIS" in cleaned
    assert "CPCIT" in cleaned
    assert "GIS 110kV" in cleaned
    assert "- Như trên;" in cleaned


def test_vietnamese_ocr_spacing_cleaner_repairs_common_splits() -> None:
    cases = {
        "Li ên kết khách hàng": "Liên kết khách hàng",
        "Tà i khoản": "Tài khoản",
        "Th anh toán": "Thanh toán",
        "H óa đơn": "Hóa đơn",
        "D anh mục Ä‘iểm thu": "Danh mục Ä‘iểm thu",
        "Q uản lý người dùng": "Quản lý người dùng",
        "D ashboard": "Dashboard",
        "báoc áo": "báo cáo",
    }

    for broken, fixed in cases.items():
        assert apply_spacing_fixes(broken) == fixed


def test_fetch_document_by_id_vb_parses_elasticsearch_response(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "hits": {
                    "hits": [
                        {"_source": {"id_vb": 1, "noi_dung": "wrong"}},
                        {
                            "_source": {
                                "id_vb": 1068586,
                                "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
                                "trich_yeu": "Triển khai GIS",
                                "noi_ban_hanh": "EVNCPC",
                                "nguoi_ky": "Nguyen Van A",
                                "ten_file": "6515.pdf",
                                "duong_dan": "/doffice/6515.pdf",
                                "noi_dung": "--- Page 1 ---\n## CPCIT\nNội dung văn bản",
                            }
                        },
                    ]
                }
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float, auth=None, verify=True) -> None:
            self.timeout = timeout
            self.auth = auth
            self.verify = verify

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            requests.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr("app.services.document_sources.document_source_doffice_elasticsearch_source.httpx.AsyncClient", FakeAsyncClient)

    document = asyncio.run(
        DofficeElasticsearchSource(url="http://example.test/_search", timeout_seconds=5).fetch_document_by_id_vb("1068586")
    )

    assert requests[0]["method"] == "GET"
    assert document.id_vb == "1068586"
    assert document.ky_hieu == "6515/EVNCPC-VTCNTT+KD+KT"
    assert document.trich_yeu == "Triển khai GIS"
    assert "--- Page" not in document.clean_text
    assert "CPCIT" in document.clean_text


class FakeRepository:
    def __init__(self, existing: SimpleNamespace | None = None, *, chunk_count: int = 7) -> None:
        self.existing = existing
        self.document: SimpleNamespace | None = None
        self.deleted_documents: list[UUID] = []
        self.raw_documents: list[SimpleNamespace] = []
        self.raw_status_updates: list[dict[str, object]] = []
        self.chunk_count = chunk_count
        self.commits = 0
        self.rollbacks = 0

    async def find_document_by_source_metadata(self, *, source_type: str, id_vb: str):
        if self.existing and source_type == DOFFICE_SOURCE_TYPE and id_vb == "1068586":
            return self.existing
        return None

    async def count_chunks_for_document(self, document_id: UUID) -> int:
        return self.chunk_count

    async def create_document(self, **kwargs):
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            title=kwargs["title"],
            source_type=kwargs["source_type"],
            status=kwargs["status"],
            organization_id=kwargs.get("organization_id"),
            document_metadata={},
            parsed_text=None,
            parsed_at=None,
        )
        return self.document

    async def update_document_parsed_content(self, document, *, parsed_text, parsed_at, status="parsed"):
        document.parsed_text = parsed_text
        document.parsed_at = parsed_at
        document.status = status
        return document

    async def update_document_metadata(self, document, metadata: dict):
        document.document_metadata = {**document.document_metadata, **metadata}
        return document

    async def delete_document(self, document) -> None:
        self.deleted_documents.append(document.id)
        self.existing = None

    async def upsert_doffice_raw_document(self, *, payload, content_hash, metadata_hash, source_type):
        raw_document = SimpleNamespace(
            id_vb=str(payload.get("id_vb") or ""),
            ky_hieu=payload.get("ky_hieu"),
            trich_yeu=payload.get("trich_yeu"),
            ngay_vb=payload.get("ngay_vb"),
            nguoi_ky=payload.get("nguoi_ky"),
            ten_file=payload.get("ten_file"),
            duong_dan=payload.get("duong_dan"),
            noi_dung_raw=payload.get("noi_dung") or "",
            tom_tat=payload.get("tom_tat"),
            raw_payload=dict(payload),
            content_hash=content_hash,
            metadata_hash=metadata_hash,
            source_type=source_type,
        )
        self.raw_documents.append(raw_document)
        return raw_document

    async def update_doffice_raw_status(self, raw_document, **statuses):
        self.raw_status_updates.append(dict(statuses))
        for key, value in statuses.items():
            setattr(raw_document, key, value)
        return raw_document

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSource:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch_document_by_id_vb(self, id_vb: str) -> DofficeDocument:
        self.calls.append(id_vb)
        return DofficeDocument(
            id_vb="1068586",
            ky_hieu="6515/EVNCPC-VTCNTT+KD+KT",
            trich_yeu="Giao nhiệm vụ GIS cho CPCIT",
            id_dv_ban_hanh=1,
            noi_ban_hanh="EVNCPC",
            nguoi_ky="Nguyen Van A",
            ten_file="6515.pdf",
            duong_dan="/doffice/6515.pdf",
            ngay_vb="2026-06-04",
            ngay_tao="2026-06-04T08:00:00",
            ngay_capnhat="2026-06-05T09:00:00",
            nam=2026,
            thang=6,
            tom_tat="Tom tat van ban GIS.",
            raw_source={
                "id_vb": "1068586",
                "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
                "trich_yeu": "Giao nhiem vu GIS cho CPCIT",
                "id_dv_ban_hanh": 1,
                "noi_ban_hanh": "EVNCPC",
                "nguoi_ky": "Nguyen Van A",
                "ten_file": "6515.pdf",
                "duong_dan": "/doffice/6515.pdf",
                "ngay_vb": "2026-06-04",
                "ngay_tao": "2026-06-04T08:00:00",
                "ngay_capnhat": "2026-06-05T09:00:00",
                "nam": 2026,
                "thang": 6,
                "tom_tat": "Tom tat van ban GIS.",
                "noi_dung": "--- Page 1 ---\n## CPCIT\nCPCIT thuc hien GIS 110kV.",
            },
            raw_noi_dung="--- Page 1 ---\n## CPCIT\nCPCIT thực hiện GIS 110kV.",
            clean_text="CPCIT\nCPCIT thực hiện GIS 110kV.",
        )


class FakeChunkingService:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def chunk_document(self, document_id: UUID, **kwargs):
        self.calls.append(document_id)
        return DocumentChunkResponse(
            document_id=document_id,
            status="chunked",
            chunk_count=2,
            preview=[ChunkPreview(chunk_index=0, content="CPCIT", start_char=0, end_char=5)],
        )


class FakeEnrichmentService:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def enrich_document(self, document_id: UUID, **kwargs):
        self.calls.append(document_id)
        return SimpleNamespace(status="enriched", enriched_count=2, skipped_count=0, failed_count=0)


class FakeVectorIndexingService:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def index_document(self, document_id: UUID, **kwargs):
        self.calls.append(document_id)
        return DocumentVectorIndexResponse(document_id=document_id, status="indexed", indexed_chunk_count=2)


class FakeVectorStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str | None]] = []

    async def delete_points_for_document(self, document_id, *, tenant_id=None) -> None:
        self.deleted.append((str(document_id), str(tenant_id) if tenant_id else None))


class FakeDocumentIndexStore:
    """Giả lập ES document index (hbrag_documents_v1) cho test 3 nhánh ingest."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.deleted: list[str] = []

    async def existing_id_vb(self, id_vb_list: list[str]) -> set[str]:
        return {v for v in id_vb_list if v in self.existing}

    async def delete_by_id_vb(self, id_vb: str) -> int:
        self.deleted.append(id_vb)
        existed = id_vb in self.existing
        self.existing.discard(id_vb)
        return 1 if existed else 0

    async def upsert_document(self, **kwargs) -> None:
        return None

    async def update_acl(self, *args, **kwargs) -> None:
        return None

    async def update_document_embedding(self, *args, **kwargs) -> None:
        return None


def _ingestion_service(
    repository: FakeRepository,
    source: FakeSource | None = None,
    *,
    document_index_store: "FakeDocumentIndexStore | None" = None,
    es_existing: set[str] | None = None,
):
    vector_store = FakeVectorStore()
    doc_index = document_index_store or FakeDocumentIndexStore(existing=es_existing)
    return (
        DofficeIngestionService(
            repository=repository,  # type: ignore[arg-type]
            source=source or FakeSource(),  # type: ignore[arg-type]
            chunking_service=FakeChunkingService(),  # type: ignore[arg-type]
            vector_indexing_service=FakeVectorIndexingService(),  # type: ignore[arg-type]
            vector_store=vector_store,  # type: ignore[arg-type]
            enrichment_service=FakeEnrichmentService(),  # type: ignore[arg-type]
            document_index_store=doc_index,  # type: ignore[arg-type]
        ),
        vector_store,
    )

def _sample_doffice_source_with_table() -> dict[str, object]:
    return {
        "id_vb": "1459570",
        "ky_hieu": "907/EVNICT-TTPM",
        "trich_yeu": "Cap nhat ung dung EVN CSKH",
        "noi_ban_hanh": "EVNICT",
        "nguoi_ky": "Nguyen Van B",
        "ten_file": "907.pdf",
        "duong_dan": "/doffice/907.pdf",
        "ngay_vb": "2026-06-04",
        "ngay_tao": "2026-06-04T08:00:00",
        "ngay_capnhat": "2026-06-05T09:00:00",
        "nam": 2026,
        "thang": 6,
        "tom_tat": "**Tom tat** nguon cho 907/EVNICT-TTPM.",
        "noi_dung": """
--- Page 1 ---
**Can cu** van ban so 3113/EVN-KDMBD ngay 02/06/2026.
Phu luc danh sach chi tiet chuc nang hieu chinh &nbsp;
<table>
  <thead>
    <tr>
      <th>STT</th><th>Nen tang</th><th>Chuc nang man hinh</th>
      <th>Noi dung hieu chinh</th><th>Giai doan</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1</td><td rowspan="3">App cham soc khach hang</td>
      <td>Li ên ket khach hang</td><td rowspan="2">Cap nhat giao dien moi</td>
      <td rowspan="2">Giai doan 1</td>
    </tr>
    <tr><td>2</td><td>Th anh toan</td></tr>
    <tr>
      <td>17</td><td>Cap dien moi ha ap</td>
      <td>Bo sung chuc nang moi</td><td>Giai doan 2</td>
    </tr>
    <tr>
      <td>30</td><td>Website Quan tri noi dung (CMS)</td><td>D ashboard</td>
      <td>Y 1<br>Y 2<br>Y 3<br>Y 4</td><td>Giai doan 2</td>
    </tr>
  </tbody>
</table>
Noi nhan
KT. GIAM DOC
PHO GIAM DOC
""",
    }

def _sample_doffice_source_with_realistic_tables() -> dict[str, object]:
    phase1 = [
        "Man hinh trang chu ung dung",
        "Li ên ket khach hang",
        "Tai khoan",
        "Thanh toan",
        "Hoa don",
        "Tra cuu dien nang",
        "Thong bao",
        "Cau hoi thuong gap",
        "Huong dan su dung",
        "Dich vu dien",
        "Lich ghi chi so",
        "Danh muc diem thu",
        "Quan ly yeu cau",
        "Thong tin khach hang",
        "Uoc tinh dien nang",
        "Bao cao su co",
    ]
    phase2 = [
        "Cap dien moi ha ap",
        "Cap dien moi trung ap",
        "Tra cuu chi so Dien MTMN",
        "Lich su thanh toan Dien MTMN",
        "Tra cuu tien do",
        "Thong bao mat dien",
        "Dang ky dich vu",
        "Quan ly tai khoan",
        "Tra cuu diem thu",
        "Danh gia dich vu",
    ]
    feature_rows = [f'<tr><td>1</td><td rowspan="26">App cham soc khach hang</td><td>{phase1[0]}</td><td rowspan="16">Cap nhat giao dien moi</td><td rowspan="16">Giai doan 1</td></tr>']
    feature_rows.extend(f"<tr><td>{index}</td><td>{name}</td></tr>" for index, name in enumerate(phase1[1:], start=2))
    feature_rows.append(f'<tr><td>17</td><td>{phase2[0]}</td><td rowspan="10">Bo sung chuc nang moi</td><td rowspan="10">Giai doan 2</td></tr>')
    feature_rows.extend(f"<tr><td>{index}</td><td>{name}</td></tr>" for index, name in enumerate(phase2[1:], start=18))
    for index, feature, change in [
        (27, "Quan ly banner", "Hieu chinh giao dien"),
        (28, "Danh muc tin tuc", "Bo sung loc du lieu"),
        (29, "Quan ly nguoi dung", "Hieu chinh phan quyen"),
        (30, "D ashboard", "Y 1<br>Y 2<br>Y 3<br>Y 4"),
        (31, "Q uan ly nhom quyen", "p<br>Bo sung tao nhom quyen theo phan cap"),
        (32, "Bao cao", "Hieu chinh báoc áo"),
        (33, "Danh muc cau hoi", "Cap nhat noi dung"),
        (34, "Quan ly log", "Bo sung tra cuu"),
    ]:
        platform = "Website Quan tri noi dung (CMS)" if index == 27 else ""
        feature_rows.append(f"<tr><td>{index}</td><td>{platform}</td><td>{feature}</td><td>{change}</td><td></td></tr>")
    ui_rows = "".join(
        f"<tr><td>{index}</td><td>{screen}</td><td>{note}</td></tr>"
        for index, screen, note in [
            (1, "Dang ky/Dang nhap", ""),
            (2, "Trang chu/dich vu va tien ich", ""),
            (3, "Tra cuu dien nang, chi so", ""),
            (4, "Thanh toan/Lich su thanh toan", ""),
            (5, "Cap dien moi ha ap/trung ap", "Tinh nang moi"),
            (6, "Tra cuu chi so Dien MTMN", "Tinh nang moi"),
            (7, "Lich su thanh toan Dien MTMN", "Tinh nang moi"),
            (8, "Tra cuu tien do", "Tinh nang moi"),
            (9, "Tra cuu diem thu", "Tinh nang moi"),
        ]
    )
    body = "\n".join(
        [
            "So: /EVNICT-TTPM",
            "Ha Noi, ngay 04 thang 6 nam 2026",
            "Can cu van ban so 3113/EVN-KDMBD ngay 02/06/2026 trien khai EVN CSKH.",
            "Danh sach chi tiet chuc nang cap nhat",
            "<table><thead><tr><th>STT</th><th>Nen tang</th><th>Chuc nang man hinh Ung dung</th><th>Noi dung hieu chinh</th><th>Giai doan</th></tr></thead><tbody>" + "".join(feature_rows) + "</tbody></table>",
            "Mot so giao dien man hinh chinh cua ung dung",
            "<table><thead><tr><th>STT</th><th>Giao dien man hinh ung dung</th><th>Ghi chu</th></tr></thead><tbody>" + ui_rows + "</tbody></table>",
            "Tran trong.",
            "Noi nhan:",
            "KT. GIAM DOC",
        ]
    )
    return {**_sample_doffice_source_with_table(), "ngay_vb": None, "noi_dung": body}


def test_doffice_normalizer_parses_tables_metadata_and_footer() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_table())

    assert "&nbsp;" not in normalized.clean_text
    assert "**" not in normalized.clean_text
    assert normalized.document_code == "907/EVNICT-TTPM"
    assert normalized.issued_date == "04/06/2026"
    assert "907/EVNICT-TTPM" in normalized.metadata["doc_codes"]
    assert "02/06/2026" not in normalized.metadata["doc_codes"]
    assert "06/2026" not in normalized.metadata["doc_codes"]
    assert "06" not in normalized.metadata["identifiers"]
    assert "NAM" not in normalized.metadata["identifiers"]
    assert any(
        ref["document_code"] == "3113/EVN-KDMBD" and ref["date"] == "02/06/2026"
        for ref in normalized.metadata["referenced_documents"]
    )
    assert normalized.metadata["document_profile"] == "doffice_admin"
    assert normalized.tables

    rows = {row.metadata.get("feature_name"): row for row in normalized.tables[0].rows}
    lien_ket = rows["Liên ket khach hang"]
    assert lien_ket.metadata["platform"] == "App cham soc khach hang"
    assert lien_ket.metadata["change_content"] == "Cap nhat giao dien moi"
    assert lien_ket.metadata["phase"] == "Giai doan 1"

    cap_dien = rows["Cap dien moi ha ap"]
    assert cap_dien.metadata["platform"] == "App cham soc khach hang"
    assert cap_dien.metadata["change_content"] == "Bo sung chuc nang moi"
    assert cap_dien.metadata["phase"] == "Giai doan 2"

    dashboard = rows["Dashboard"]
    assert dashboard.metadata["platform"] == "Website Quan tri noi dung (CMS)"
    assert dashboard.metadata["change_content"] == ["Y 1", "Y 2", "Y 3", "Y 4"]
    assert any(element.element_type == "footer_signature" for element in normalized.elements)

def test_doffice_chunk_builder_keeps_table_rows_structured() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_table())
    chunks = build_doffice_chunks(normalized)
    forbidden = {
        "noi_dung_raw",
        "plain_text",
        "markdown_text",
        "tom_tat",
        "parsed_elements",
        "raw_source_metadata",
        "raw_payload",
        "enrichment",
        "raw_cells",
    }

    chunk_types = [chunk.metadata["chunk_type"] for chunk in chunks]
    # Tóm tắt tổng hợp đã BỎ khỏi collection chunk (chỉ giữ nội dung thật).
    assert "document_summary" not in chunk_types
    assert "document_header" in chunk_types
    assert "document_body" in chunk_types
    # Builder v2: mỗi bảng chỉ còn 1 chunk_type="table" (không nổ table_parent/table_row).
    assert "table" in chunk_types
    assert chunk_types.count("table") == 1
    assert "table_parent" not in chunk_types
    assert "table_row" not in chunk_types
    assert all(forbidden.isdisjoint(chunk.metadata) for chunk in chunks)

    body = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "document_body")
    assert "Tóm tắt nguồn" not in body.content
    assert "PHU LUC" not in body.content
    assert "[[TABLE_1]]" not in body.content

    table = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "table")
    assert table.metadata["row_count"] == 4
    # Dòng ngữ cảnh đầu chunk: tên bảng + ký hiệu văn bản.
    assert "Bảng:" in table.content
    assert "Văn bản: 907/EVNICT-TTPM" in table.content
    assert "chức năng/màn hình" in table.content
    assert "nội dung hiệu chỉnh/bổ sung" in table.content
    assert not any(marker in table.content for marker in ("Báº", "Sá»", "CÃ", "NhÃ", "dÃ²ng", "cá»"))
    # Bảng giờ là 1 chunk chứa ĐẦY ĐỦ dữ liệu dòng (markdown), không còn nổ table_row.
    assert "Cap dien moi ha ap" in table.content
    assert "Bo sung chuc nang moi" in table.content
    assert table.metadata["table_title"] == table.metadata["table_name"]
    assert table.metadata["table_headers"]
    assert table.metadata["source_type"] == DOFFICE_SOURCE_TYPE
    assert table.metadata["id_vb"] == "1459570"
    assert table.metadata["document_code"] == "907/EVNICT-TTPM"
    assert table.metadata["trich_yeu"] == "Cap nhat ung dung EVN CSKH"
    assert table.metadata["indexable"] is True

    footer = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "footer_signature")
    assert "PHU LUC" not in footer.content
    assert "[[TABLE_1]]" not in footer.content
    assert footer.metadata["is_footer_or_signature"] is True
    assert footer.metadata["indexable"] is False
    assert footer.metadata["embedding_enabled"] is False

def _sample_doffice_source_with_appendix() -> dict[str, object]:
    """Mô phỏng thể thức 6515: thân văn bản + footer + phụ lục có heading riêng.

    Heading phụ lục ("Phụ lục 01", "(1) F08_CotDien_HT – Lớp cột điện", "Tên bảng dữ
    liệu...") KHÔNG khớp các từ khóa cũ ("danh sách"/"giao màn") nên trước fix các bảng
    này rơi về tên "Bảng DOffice N" và prose phụ lục bị bỏ hoàn toàn.
    """

    return {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
        "trich_yeu": "Ke hoach xay dung he thong GIS chuan hoa CSDL luoi dien",
        "noi_ban_hanh": "Tong cong ty Dien luc mien Trung",
        "nguoi_ky": "Le Hoang Anh Dung",
        "ngay_vb": "2025-08-21",
        "noi_dung": """
Kinh gui: Cac don vi thanh vien.
Tong cong ty giao nhiem vu cho cac don vi thuc hien cac noi dung sau.
Tran trong./.
KT. TONG GIAM DOC
Noi nhan:
- Nhu tren;
- Luu: VT, VTCNTT, KT, KD.
PHU LUC 01
PHUONG AN SAP NHAP DU LIEU GIS 110kV, GIS TRUNG THE
<table><thead><tr><th>TT</th><th>Du lieu</th><th>CPCIT</th></tr></thead>
<tbody><tr><td>1</td><td>GIS 110kV</td><td>Sap nhap du lieu</td></tr></tbody></table>
Phu luc 02
MO TA DU LIEU KHOI TAO VA CHUYEN DOI SANG DU LIEU GIS HA THE
1. Muc tieu
Khoi tao khung CSDL GIS luoi dien ha the bao gom 10 doi tuong thiet bi.
2. Chi tiet du lieu chuyen doi cua 07 doi tuong thiet bi
(1) F08_CotDien_HT – Lop cot dien
<table><thead><tr><th>TT</th><th>Truong du lieu</th><th>Mo ta</th><th>Kieu du lieu</th></tr></thead>
<tbody><tr><td>1</td><td>ID</td><td>ID Cot dien</td><td>Text</td></tr></tbody></table>
""",
    }


def test_doffice_appendix_prose_and_table_titles_are_captured() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_appendix())
    chunks = build_doffice_chunks(normalized)

    # FIX: bảng phụ lục nhận tên theo heading thật, không còn "Bảng DOffice N".
    table_names = {table.metadata.get("table_name") for table in normalized.tables}
    assert not any(str(name).startswith("Bảng DOffice") for name in table_names)
    assert any("F08_CotDien_HT" in str(name) for name in table_names)

    # Prose phụ lục có heading "1./2." nên heading-aware chunker phân loại thành
    # document_section; gom theo cờ artifact_type thay vì chunk_type.
    appendix_chunks = [c for c in chunks if c.metadata.get("artifact_type") == "appendix"]
    appendix_text = "\n".join(c.content for c in appendix_chunks)
    # FIX: prose phụ lục (tiêu đề + mục tiêu + heading lớp dữ liệu) không còn bị bỏ.
    assert appendix_chunks
    assert "Muc tieu" in appendix_text
    assert "F08_CotDien_HT" in appendix_text

    # FIX: chunk bảng mang đầy đủ khối ngữ cảnh như chunk prose.
    table_chunk = next(c for c in chunks if c.metadata.get("chunk_type") == "table")
    assert "Văn bản: 6515/EVNCPC-VTCNTT+KD+KT" in table_chunk.content
    assert "Ngày ban hành: 21/08/2025" in table_chunk.content
    assert "Cơ quan ban hành: Tong cong ty Dien luc mien Trung" in table_chunk.content
    assert "Bảng:" in table_chunk.content


def test_doffice_summary_filters_pii_and_table_rows_are_lookup_ready() -> None:
    source = {
        "id_vb": "608",
        "ky_hieu": "608/QĐ-IT",
        "trich_yeu": "Quyết định cử cán bộ tham gia khóa đào tạo Ứng dụng Python trên nền tảng ArcGIS",
        "noi_ban_hanh": "CPCIT",
        "ngay_vb": "2026-06-10",
        "tom_tat": "Quyết định cử 06 cán bộ đi đào tạo. Nguyễn Thanh Phú 0983129374 phunt3@cpc.vn; danh sách cán bộ PM kèm theo.",
        "noi_dung": """
Điều 1. Cử cán bộ tham gia khóa đào tạo Ứng dụng Python trên nền tảng ArcGIS.
Thời gian đào tạo từ ngày 17/06/2026 đến ngày 19/06/2026.
Địa điểm đào tạo: Hà Nội.
Đơn vị đào tạo: ESRI Việt Nam.
Kinh phí do CPCIT chi trả.
Danh sách cán bộ tham gia
<table>
<tr><th>STT</th><th>Họ tên</th><th>Chức vụ</th><th>Phòng</th><th>Điện thoại</th><th>Email</th></tr>
<tr><td>1</td><td>Nguyễn Thanh Phú</td><td>Chuyên viên</td><td>VH</td><td>0983129374</td><td>phunt3@cpc.vn</td></tr>
<tr><td>2</td><td>Trần Văn B</td><td>Chuyên viên</td><td>PM</td><td>0912345678</td><td>b@cpc.vn</td></tr>
</table>
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    table = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "table")

    # Tóm tắt KHÔNG còn là chunk (chỉ giữ nội dung), nhưng vẫn được sinh + lọc PII.
    assert not any(chunk.metadata.get("chunk_type") == "document_summary" for chunk in chunks)
    summary_text = normalized.summary_text or ""
    assert "0983129374" not in summary_text
    assert "phunt3@cpc.vn" not in summary_text
    assert "Nguyễn Thanh Phú" not in summary_text
    assert len(summary_text.split()) <= 200
    for chunk in chunks:
        if chunk.metadata.get("chunk_type") in {"document_header", "legal_clause", "table"}:
            assert chunk.metadata["source_span"]["start"] <= chunk.metadata["source_span"]["end"]

    # Danh sách cán bộ (kể cả PII) nằm đầy đủ trong chunk bảng để tra cứu.
    assert "Nguyễn Thanh Phú" in table.content
    assert "Chuyên viên" in table.content
    assert "0983129374" in table.content
    assert "phunt3@cpc.vn" in table.content
def test_doffice_html_tables_keep_rowspan_context_and_chunk_rows() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_realistic_tables())

    assert normalized.issued_date == "04/06/2026"
    assert "|" not in normalized.clean_text
    assert len(normalized.tables) == 2
    assert len(normalized.tables[0].rows) == 34
    assert len(normalized.tables[1].rows) == 9
    assert len(normalized.table_rows) == 43

    rows = {row.metadata["row_number"]: row for row in normalized.tables[0].rows}
    assert rows["2"].metadata["feature_name"] == "Liên ket khach hang"
    assert rows["2"].metadata["change_content"] == "Cap nhat giao dien moi"
    assert rows["2"].metadata["phase"] == "Giai doan 1"
    assert rows["17"].metadata["feature_name"] == "Cap dien moi ha ap"
    assert rows["17"].metadata["change_content"] == "Bo sung chuc nang moi"
    assert rows["17"].metadata["phase"] == "Giai doan 2"
    assert rows["25"].metadata["feature_name"] == "Tra cuu diem thu"
    assert rows["25"].metadata["phase"] == "Giai doan 2"
    assert rows["30"].metadata["feature_name"] == "Dashboard"
    assert rows["30"].metadata["change_content"] == ["Y 1", "Y 2", "Y 3", "Y 4"]
    assert rows["31"].metadata["change_content"] == "Bo sung tao nhom quyen theo phan cap"

    ui_rows = {row.metadata["row_number"]: row for row in normalized.tables[1].rows}
    assert ui_rows["3"].metadata["screen_name"] == "Tra cuu dien nang, chi so"

    element_types = [element.element_type for element in normalized.elements]
    for element_type in ["document_header", "document_body", "footer_signature", "table_parent", "table_row"]:
        assert element_type in element_types
    assert next(element for element in normalized.elements if element.element_type == "footer_signature").metadata["indexable"] is False

    chunks = build_doffice_chunks(normalized)
    # Builder v2: bảng -> chunk_type="table" (không còn table_row/table_group/table_column).
    table_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table"]
    assert table_chunks
    assert not any(
        chunk.metadata.get("chunk_type") in {"table_row", "table_group", "table_column", "table_parent"}
        for chunk in chunks
    )
    combined = "\n".join(chunk.content for chunk in table_chunks)
    # Dữ liệu các dòng/nhóm vẫn nằm đầy đủ trong nội dung markdown của chunk bảng.
    assert "Dashboard" in combined
    assert "Cap dien moi ha ap" in combined
    assert "Tra cuu dien nang, chi so" in combined
    assert all(chunk.metadata.get("table_name") for chunk in table_chunks)
    assert all(chunk.metadata.get("source_span") for chunk in table_chunks)


def test_ingest_doffice_document_creates_document_metadata() -> None:
    repository = FakeRepository()
    service, _ = _ingestion_service(repository)

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=True),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
            access={"scope": "corp_wide"},
        )
    )

    assert response.status == "success"
    assert response.chunks_created == 2
    assert repository.document is not None
    assert not repository.document.parsed_text.startswith("Tóm tắt nguồn:")
    assert "CPCIT" in repository.document.parsed_text
    metadata = repository.document.document_metadata
    assert metadata["source_type"] == DOFFICE_SOURCE_TYPE
    assert metadata["id_vb"] == "1068586"
    assert metadata["ky_hieu"] == "6515/EVNCPC-VTCNTT+KD+KT"
    assert metadata["doc_code"] == "6515/EVNCPC-VTCNTT+KD+KT"
    assert "1068586" in metadata["identifiers"]
    assert "6515" in metadata["identifiers"]
    assert metadata["parsed_elements"][0]["element_type"] == "document_summary"
    assert repository.raw_documents
    raw_document = repository.raw_documents[0]
    assert raw_document.id_vb == "1068586"
    assert raw_document.ky_hieu == "6515/EVNCPC-VTCNTT+KD+KT"
    assert raw_document.ngay_vb == "2026-06-04"
    assert raw_document.nguoi_ky == "Nguyen Van A"
    assert raw_document.ten_file == "6515.pdf"
    assert raw_document.duong_dan == "/doffice/6515.pdf"
    assert raw_document.noi_dung_raw == raw_document.raw_payload["noi_dung"]
    assert raw_document.tom_tat == "Tom tat van ban GIS."
    assert {"parse_status": "normalized", "clean_status": "cleaned"} in repository.raw_status_updates
    assert {"chunk_status": "chunked"} in repository.raw_status_updates
    assert {"embedding_status": "indexed", "sync_status": "indexed"} in repository.raw_status_updates


def _existing_synced_doc(*, with_noi_dung: bool = True) -> SimpleNamespace:
    meta = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
        "trich_yeu": "Giao nhiem vu GIS",
        "noi_ban_hanh": "EVNCPC",
        "has_embedding": True,
        "access": {"raw_assignment": {"don_vi_list": [], "phong_ban_list": [], "ca_nhan_list": []}},
    }
    if with_noi_dung:
        meta["noi_dung_raw"] = "--- Page 1 ---\n## CPCIT\nCPCIT thuc hien GIS 110kV."
    return SimpleNamespace(
        id=EXISTING_DOCUMENT_ID,
        organization_id=ORG_ID,
        document_profile=None,
        parsed_text=None,
        parsed_at=None,
        document_metadata=meta,
    )


def test_ingest_doffice_both_db_exist_and_already_chunked_skips() -> None:
    """Cả 2 DB đều có VÀ đã có chunk -> tái sử dụng, không chunk lại, không gọi DOffice."""
    existing = _existing_synced_doc()
    source = FakeSource()
    repository = FakeRepository(existing=existing, chunk_count=7)
    service, vector_store = _ingestion_service(repository, source, es_existing={"1068586"})

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "skipped"
    assert response.document_id == EXISTING_DOCUMENT_ID
    assert response.chunks_created == 7
    assert source.calls == []
    assert repository.deleted_documents == []
    assert repository.document is None  # không tạo document mới
    assert vector_store.deleted == []


def test_ingest_doffice_both_db_exist_no_chunks_reads_noi_dung_from_pg() -> None:
    """Cả 2 DB đều có nhưng chưa chunk -> đọc noi_dung từ PG, chunk + Qdrant, không tạo doc."""
    existing = _existing_synced_doc(with_noi_dung=True)
    source = FakeSource()
    repository = FakeRepository(existing=existing, chunk_count=0)
    service, vector_store = _ingestion_service(repository, source, es_existing={"1068586"})

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "success"
    assert response.document_id == EXISTING_DOCUMENT_ID
    assert response.chunks_created == 2
    assert source.calls == []  # đọc noi_dung từ PG, KHÔNG gọi DOffice
    assert repository.document is None  # KHÔNG tạo document mới
    assert repository.deleted_documents == []
    assert vector_store.deleted == []
    # đã nạp nội dung normalized vào document ĐANG CÓ
    assert "CPCIT" in (existing.parsed_text or "")
    assert existing.document_metadata.get("parsed_elements")
    # giữ nguyên ACL đã sync (không bị xóa)
    assert existing.document_metadata.get("access")


def test_ingest_doffice_both_db_exist_missing_noi_dung_falls_back_to_doffice() -> None:
    """Cả 2 DB đều có nhưng PG chưa lưu noi_dung_raw -> lấy 1 lần từ DOffice rồi cache."""
    existing = _existing_synced_doc(with_noi_dung=False)
    source = FakeSource()
    repository = FakeRepository(existing=existing, chunk_count=0)
    service, _vector_store = _ingestion_service(repository, source, es_existing={"1068586"})

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "success"
    assert source.calls == ["1068586"]  # thiếu noi_dung_raw -> fetch DOffice 1 lần
    assert existing.document_metadata.get("noi_dung_raw")  # đã cache vào PG
    assert repository.document is None


def test_ingest_doffice_xor_pg_only_deletes_and_full_ingests() -> None:
    """Lệch: chỉ PG có (ES mất) -> xóa phần thừa PG rồi ingest đầy đủ từ DOffice."""
    existing = _existing_synced_doc()
    source = FakeSource()
    repository = FakeRepository(existing=existing)
    service, vector_store = _ingestion_service(repository, source, es_existing=set())

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "success"
    assert repository.deleted_documents == [EXISTING_DOCUMENT_ID]  # xóa PG thừa
    assert source.calls == ["1068586"]  # ingest đầy đủ từ DOffice
    assert repository.document is not None  # tạo document mới
    assert vector_store.deleted == [(str(EXISTING_DOCUMENT_ID), str(ORG_ID))]


def test_ingest_doffice_xor_es_only_deletes_es_and_full_ingests() -> None:
    """Lệch: chỉ ES có (PG mất) -> xóa record ES thừa rồi ingest đầy đủ từ DOffice."""
    source = FakeSource()
    repository = FakeRepository()  # PG không có
    doc_index = FakeDocumentIndexStore(existing={"1068586"})  # ES có
    service, _vector_store = _ingestion_service(repository, source, document_index_store=doc_index)

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=False, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "success"
    assert doc_index.deleted == ["1068586"]  # xóa record ES thừa
    assert source.calls == ["1068586"]  # ingest đầy đủ
    assert repository.document is not None


def test_ingest_doffice_document_force_refresh_replaces_existing() -> None:
    existing = SimpleNamespace(
        id=EXISTING_DOCUMENT_ID,
        organization_id=ORG_ID,
        document_metadata={"id_vb": "1068586"},
    )
    source = FakeSource()
    repository = FakeRepository(existing=existing)
    service, vector_store = _ingestion_service(repository, source)

    response = asyncio.run(
        service.ingest_doffice_document(
            "1068586",
            DofficeIngestOptions(force_refresh=True, enable_enrichment=False),
            uploaded_by_user_id=USER_ID,
            organization_id=ORG_ID,
            knowledge_base_id=KB_ID,
        )
    )

    assert response.status == "success"
    assert source.calls == ["1068586"]
    assert repository.deleted_documents == [EXISTING_DOCUMENT_ID]
    assert vector_store.deleted == [(str(EXISTING_DOCUMENT_ID), str(ORG_ID))]


def test_identifier_retrieval_boosts_doffice_metadata() -> None:
    metadata = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
        "identifiers": ["1068586", "6515", "6515/EVNCPC-VTCNTT+KD+KT"],
        "doc_codes": ["6515/EVNCPC-VTCNTT+KD+KT"],
    }

    assert identifier_exact_match_boost("1068586", "", metadata) >= IDENTIFIER_EXACT_BOOST
    assert identifier_exact_match_boost("6515", "", metadata) >= IDENTIFIER_EXACT_BOOST
    assert identifier_exact_match_boost("6515/EVNCPC-VTCNTT+KD+KT", "", metadata) >= IDENTIFIER_EXACT_BOOST
    assert "6515/EVNCPC-VTCNTT+KD+KT" in KeywordSearchService._extract_exact_terms("6515/EVNCPC-VTCNTT+KD+KT")


def test_doffice_ingest_endpoint_uses_service_override() -> None:
    class FakeLogRepository:
        def __init__(self) -> None:
            self.pipeline_logs = []

        async def create_pipeline_log(self, **kwargs):
            self.pipeline_logs.append(kwargs)
            return SimpleNamespace(**kwargs)

        async def commit(self) -> None:
            return None

    class FakeDofficeEndpointService:
        async def ingest_doffice_document(self, id_vb, options, **kwargs):
            assert id_vb == "1068586"
            assert options.force_refresh is False
            assert options.enable_enrichment is True
            return DofficeIngestResponse(
                status="success",
                id_vb="1068586",
                ky_hieu="6515/EVNCPC-VTCNTT+KD+KT",
                trich_yeu="Giao nhiệm vụ GIS",
                noi_ban_hanh="EVNCPC",
                chunks_created=2,
                document_id=DOCUMENT_ID,
                source_type=DOFFICE_SOURCE_TYPE,
                message="ok",
            )

    log_repository = FakeLogRepository()
    app.dependency_overrides[get_doffice_ingestion_service] = lambda: FakeDofficeEndpointService()
    app.dependency_overrides[get_document_log_repository] = lambda: log_repository
    try:
        response = TestClient(app).post(
            "/api/documents/doffice/ingest",
            json={"id_vb": "1068586", "force_refresh": False, "enable_enrichment": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["id_vb"] == "1068586"
    assert payload["chunks_created"] == 2
    assert log_repository.pipeline_logs[0]["action"] == "parse"
    assert log_repository.pipeline_logs[0]["status"] == "success"
    assert log_repository.pipeline_logs[0]["metadata"]["pipeline_action"] == "doffice_ingest"


# ===========================================================================
# Chunking fixes — DOffice pipeline (Fix 1/2/3)
# ===========================================================================

def _minimal_normalized_doc(tables: list[NormalizedTable]) -> NormalizedDofficeDocument:
    return NormalizedDofficeDocument(
        id_vb="1068586",
        document_code="6515/EVNCPC",
        title="Kế hoạch GIS",
        issued_date="11/08/2025",
        issuer="Tổng công ty Điện lực miền Trung",
        signer=None,
        raw_text="",
        clean_text="",
        markdown_text="",
        plain_text="",
        summary_text=None,
        elements=[],
        tables=tables,
        metadata={"document_code": "6515/EVNCPC", "ky_hieu": "6515/EVNCPC", "id_vb": "1068586"},
        content_hash="hash",
        metadata_hash="mhash",
    )


def _big_table_markdown(rows: int) -> str:
    header = "| TT | Tên trường | Mô tả | Kiểu dữ liệu | Nguồn |\n| --- | --- | --- | --- | --- |"
    body = "\n".join(
        f"| {i} | Truong_{i} | Mo ta chi tiet cua truong du lieu so {i} | Text | Bien tap |"
        for i in range(1, rows + 1)
    )
    return f"{header}\n{body}"


# --- FIX 1: bỏ chunk chỉ là tiêu đề mục rỗng -------------------------------

def test_is_section_title_only_chunk_detects_empty_heading() -> None:
    content = (
        "Văn bản: 6515/EVNCPC - Kế hoạch GIS\n"
        "Ngày ban hành: 11/08/2025\n"
        "Cơ quan ban hành: Tổng công ty Điện lực miền Trung\n"
        "Mục: 1. CPCIT:\n"
        "1. CPCIT:"
    )
    metadata = {"chunk_type": "document_section", "section_title": "1. CPCIT:"}
    assert _is_section_title_only_chunk(content, metadata) is True


def test_is_section_title_only_chunk_keeps_section_with_body() -> None:
    content = (
        "Văn bản: 6515/EVNCPC - Kế hoạch GIS\n"
        "Mục: 1.1. GIS 110kV:\n"
        "1.1. GIS 110kV:\n"
        "- Hiệu chỉnh phần mềm PMISToGIS hoàn thành trong tháng 10/2025.\n"
        "- Tổ chức hướng dẫn sử dụng công cụ cho các đơn vị liên quan."
    )
    metadata = {"chunk_type": "document_section", "section_title": "1.1. GIS 110kV:"}
    assert _is_section_title_only_chunk(content, metadata) is False


def test_is_section_title_only_chunk_ignores_other_chunk_types() -> None:
    content = "Số/ký hiệu: 6515/EVNCPC\nTrích yếu: Kế hoạch GIS"
    metadata = {"chunk_type": "document_header"}
    assert _is_section_title_only_chunk(content, metadata) is False


# --- FIX 2: bảng lớn — continuation context + gộp mảnh cuối quá ngắn --------

def test_table_chunks_add_part_suffix_when_split() -> None:
    markdown = _big_table_markdown(80)
    assert len(markdown) > TABLE_CHUNK_MAX_CHARS
    table = NormalizedTable(
        table_index=0,
        headers=["TT", "Tên trường", "Mô tả", "Kiểu dữ liệu", "Nguồn"],
        rows=[],
        markdown=markdown,
        text="",
        metadata={"table_name": "Bảng 1", "table_index": 0},
    )
    results = _table_chunks(_minimal_normalized_doc([table]), table)
    assert len(results) > 1
    # Khối ngữ cảnh đầu chunk bảng giờ mang đầy đủ thông tin văn bản như chunk prose.
    assert "Văn bản:" in results[0][0]
    assert "Ngày ban hành:" in results[0][0]
    assert "Cơ quan ban hành:" in results[0][0]
    assert "Phần: 1/" in results[0][0]
    assert "Phần: 2/" in results[1][0]
    assert results[1][1]["subchunk_index"] == 1
    assert results[1][1]["chunk_strategy"] == "table_chunker_split"


def test_table_chunks_single_when_small() -> None:
    table = NormalizedTable(
        table_index=0,
        headers=["TT", "Tên trường", "Mô tả", "Kiểu dữ liệu", "Nguồn"],
        rows=[],
        markdown=_big_table_markdown(3),
        text="",
        metadata={"table_name": "Bảng 1"},
    )
    results = _table_chunks(_minimal_normalized_doc([table]), table)
    assert len(results) == 1
    assert "Phần:" not in results[0][0]
    assert results[0][1]["chunk_strategy"] == "table_single"


def test_merge_tiny_last_table_piece_merges_single_row_tail() -> None:
    header = "| TT | Tên |\n| --- | --- |"
    p1 = f"{header}\n| 1 | A |\n| 2 | B |\n| 3 | C |"
    p2 = f"{header}\n| 4 | D |"
    merged = _merge_tiny_last_table_piece([p1, p2])
    assert len(merged) == 1
    assert "| 4 | D |" in merged[0]
    assert merged[0].count("| --- |") == 1


def test_merge_tiny_last_table_piece_keeps_large_tail() -> None:
    header = "| TT | Tên |\n| --- | --- |"
    p1 = f"{header}\n| 1 | A |\n| 2 | B |"
    p2 = f"{header}\n| 3 | C |\n| 4 | D |\n| 5 | E |"
    merged = _merge_tiny_last_table_piece([p1, p2])
    assert len(merged) == 2


# --- FIX 3: issued_date dạng số có tiền tố "ngày" --------------------------

def test_normalizer_extracts_numeric_issued_date() -> None:
    source = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT",
        "trich_yeu": "Kế hoạch xây dựng hệ thống GIS",
        "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
        "ngay_vb": None,
        "noi_dung": "<p>Ngày 11/8/2025, HĐTV EVNCPC đã ban hành Nghị quyết số 720/NQ-HĐTV.</p>",
    }
    normalized = normalize_doffice_source(source)
    assert normalized.issued_date == "11/08/2025"


def test_normalizer_issued_date_propagates_to_chunks() -> None:
    source = {
        "id_vb": "1068586",
        "ky_hieu": "6515/EVNCPC-VTCNTT",
        "trich_yeu": "Kế hoạch xây dựng hệ thống GIS",
        "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
        "ngay_vb": None,
        "noi_dung": "<p>Ngày 11/8/2025, HĐTV EVNCPC đã ban hành Nghị quyết số 720.</p>",
    }
    chunks = build_doffice_chunks(normalize_doffice_source(source))
    assert chunks
    assert any(c.metadata.get("issued_date") == "11/08/2025" for c in chunks)
