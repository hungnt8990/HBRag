from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_log_repository, get_doffice_ingestion_service
from app.main import app
from app.schemas.documents import ChunkPreview, DocumentChunkResponse, DocumentVectorIndexResponse, DofficeIngestResponse
from app.services.document_sources import DOFFICE_SOURCE_TYPE, DofficeDocument, DofficeElasticsearchSource
from app.services.doffice_chunking import build_doffice_chunks
from app.services.doffice_content_normalizer import apply_spacing_fixes, normalize_doffice_source
from app.services.doffice_ingestion_service import DofficeIngestionService, DofficeIngestOptions
from app.services.hybrid_search import IDENTIFIER_EXACT_BOOST, identifier_exact_match_boost
from app.services.keyword_search import KeywordSearchService
from app.services.text_cleaning import clean_doffice_markdown_to_text

DOCUMENT_ID = UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
EXISTING_DOCUMENT_ID = UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
USER_ID = UUID("cccccccc-3333-3333-3333-cccccccccccc")
ORG_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
KB_ID = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")


def test_doffice_known_mojibake_repairs_are_targeted() -> None:
    broken = "Ch\u053c\u0575 đổi sang GIS"
    assert apply_spacing_fixes(broken) == "Chuyển đổi sang GIS"
    assert apply_spacing_fixes("Chuyển đổi sang GIS") == "Chuyển đổi sang GIS"
    assert apply_spacing_fixes("Trường dữ liệu") == "Trường dữ liệu"
    assert apply_spacing_fixes("DÃ²ng | Ngá»¯ cáº£nh hÃ ng | Ná»™i dung cá»™t") == (
        "Dòng | Ngữ cảnh hàng | Nội dung cột"
    )


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
        "Li Ãªn káº¿t khÃ¡ch hÃ ng": "LiÃªn káº¿t khÃ¡ch hÃ ng",
        "TÃ  i khoáº£n": "TÃ i khoáº£n",
        "Th anh toÃ¡n": "Thanh toÃ¡n",
        "H Ã³a Ä‘Æ¡n": "HÃ³a Ä‘Æ¡n",
        "D anh má»¥c Ä‘iá»ƒm thu": "Danh má»¥c Ä‘iá»ƒm thu",
        "Q uáº£n lÃ½ ngÆ°á»i dÃ¹ng": "Quáº£n lÃ½ ngÆ°á»i dÃ¹ng",
        "D ashboard": "Dashboard",
        "bÃ¡oc Ã¡o": "bÃ¡o cÃ¡o",
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
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs):
            requests.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr("app.services.document_sources.doffice_elasticsearch_source.httpx.AsyncClient", FakeAsyncClient)

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
    def __init__(self, existing: SimpleNamespace | None = None) -> None:
        self.existing = existing
        self.document: SimpleNamespace | None = None
        self.deleted_documents: list[UUID] = []
        self.raw_documents: list[SimpleNamespace] = []
        self.raw_status_updates: list[dict[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    async def find_document_by_source_metadata(self, *, source_type: str, id_vb: str):
        if self.existing and source_type == DOFFICE_SOURCE_TYPE and id_vb == "1068586":
            return self.existing
        return None

    async def count_chunks_for_document(self, document_id: UUID) -> int:
        return 7

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


def _ingestion_service(repository: FakeRepository, source: FakeSource | None = None):
    vector_store = FakeVectorStore()
    return (
        DofficeIngestionService(
            repository=repository,  # type: ignore[arg-type]
            source=source or FakeSource(),  # type: ignore[arg-type]
            chunking_service=FakeChunkingService(),  # type: ignore[arg-type]
            vector_indexing_service=FakeVectorIndexingService(),  # type: ignore[arg-type]
            vector_store=vector_store,  # type: ignore[arg-type]
            enrichment_service=FakeEnrichmentService(),  # type: ignore[arg-type]
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
      <td>Li Ãªn ket khach hang</td><td rowspan="2">Cap nhat giao dien moi</td>
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
        "Li Ãªn ket khach hang",
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
        (32, "Bao cao", "Hieu chinh bÃ¡oc Ã¡o"),
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
    lien_ket = rows["LiÃªn ket khach hang"]
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
    assert "document_summary" in chunk_types
    assert "document_header" in chunk_types
    assert "document_body" in chunk_types
    assert "table_parent" in chunk_types
    assert chunk_types.count("table_row") == 4
    assert all(forbidden.isdisjoint(chunk.metadata) for chunk in chunks)

    summary = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "document_summary")
    assert len(summary.content) < 1000
    assert "|" not in summary.content

    body = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "document_body")
    assert "Tóm tắt nguồn" not in body.content
    assert "Phu luc danh sach chi tiet chuc nang hieu chinh" in body.content
    assert "[[TABLE_1]]" not in body.content

    table_parent = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "table_parent")
    assert table_parent.metadata["row_count"] == 4
    assert "Bảng:" in table_parent.content
    assert "Số dòng: 4" in table_parent.content
    assert "Các cột chuẩn hóa:" in table_parent.content
    assert "chức năng/màn hình" in table_parent.content
    assert "nội dung hiệu chỉnh/bổ sung" in table_parent.content
    assert "Nhóm chính:" in table_parent.content
    assert "Bảng Markdown xem trước:" in table_parent.content
    assert "| STT | nền tảng | chức năng/màn hình | nội dung hiệu chỉnh/bổ sung | giai đoạn |" in table_parent.content
    assert not any(marker in table_parent.content for marker in ("Báº", "Sá»", "CÃ", "NhÃ", "dÃ²ng", "cá»"))

    cap_dien = next(chunk for chunk in chunks if chunk.metadata.get("feature_name") == "Cap dien moi ha ap")
    assert cap_dien.metadata["platform"] == "App cham soc khach hang"
    assert cap_dien.metadata["change_content"] == "Bo sung chuc nang moi"
    assert cap_dien.metadata["phase"] == "Giai doan 2"
    assert cap_dien.metadata["source_type"] == DOFFICE_SOURCE_TYPE
    assert cap_dien.metadata["id_vb"] == "1459570"
    assert cap_dien.metadata["document_code"] == "907/EVNICT-TTPM"
    assert cap_dien.metadata["trich_yeu"] == "Cap nhat ung dung EVN CSKH"
    assert cap_dien.metadata["is_table_row"] is True
    assert cap_dien.metadata["indexable"] is True
    assert "Dữ liệu dòng (Markdown table):" in cap_dien.content
    assert "| Cột | Nội dung |" in cap_dien.content
    assert "| Chuc nang man hinh | Cap dien moi ha ap |" in cap_dien.content

    footer = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "footer_signature")
    assert "PHU LUC" not in footer.content
    assert "[[TABLE_1]]" not in footer.content
    assert footer.metadata["is_footer_or_signature"] is True
    assert footer.metadata["indexable"] is False
    assert footer.metadata["embedding_enabled"] is False


def test_doffice_text_body_is_split_by_sections_with_document_preamble() -> None:
    source = {
        "id_vb": "1479034",
        "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
        "trich_yeu": "Kế hoạch xây dựng hệ thống GIS chuẩn hóa cơ sở dữ liệu lưới điện của EVNCPC",
        "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
        "ngay_vb": "2025-08-21",
        "noi_dung": """
1. Mục tiêu
Triển khai chuẩn hóa dữ liệu GIS.

1.1. Phạm vi thực hiện
Thực hiện trên các đơn vị được phân công.

1.2. Yêu cầu dữ liệu
Dữ liệu phải được rà soát và cập nhật.

2. Tổ chức thực hiện
CPCIT phối hợp với các đơn vị liên quan.
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    body_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "document_body"]

    assert len(body_chunks) == 3
    assert [chunk.metadata.get("section_title") for chunk in body_chunks] == [
        "1.1. Phạm vi thực hiện",
        "1.2. Yêu cầu dữ liệu",
        "2. Tổ chức thực hiện",
    ]
    first_body = body_chunks[0]
    assert first_body.content.startswith(
        "Văn bản: 6515/EVNCPC-VTCNTT+KD+KT - Kế hoạch xây dựng hệ thống GIS chuẩn hóa cơ sở dữ liệu lưới điện của EVNCPC\n"
        "Ngày ban hành: 21/08/2025\n"
        "Cơ quan ban hành: Tổng công ty Điện lực miền Trung"
    )
    assert "1. Mục tiêu" in first_body.content
    assert "Triển khai chuẩn hóa dữ liệu GIS." in first_body.content
    assert "1.1. Phạm vi thực hiện" in first_body.content
    assert "Triển khai chuẩn hóa dữ liệu GIS.\n1.1. Phạm vi thực hiện" in first_body.content
    assert "Triển khai chuẩn hóa dữ liệu GIS.\n\n1.1. Phạm vi thực hiện" not in first_body.content
    assert first_body.metadata["heading_path"] == ["1. Mục tiêu", "1.1. Phạm vi thực hiện"]
    assert body_chunks[1].metadata["heading_path"] == ["1. Mục tiêu", "1.2. Yêu cầu dữ liệu"]


def test_doffice_parent_section_with_significant_content_keeps_own_chunk() -> None:
    parent_content = (
        "Các đơn vị phải hoàn thành việc rà soát dữ liệu trước ngày 30/09/2025. "
        "Nội dung phối hợp bao gồm chuẩn hóa dữ liệu, kiểm tra định kỳ, lập báo cáo "
        "và cập nhật kết quả theo đúng yêu cầu của Tổng công ty. "
        "Trường hợp phát sinh vướng mắc, đơn vị phải phản hồi bằng văn bản để xử lý."
    )
    source = {
        "id_vb": "1479035",
        "ky_hieu": "6516/EVNCPC-VTCNTT+KD+KT",
        "trich_yeu": "Kế hoạch phối hợp dữ liệu GIS",
        "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
        "ngay_vb": "2025-08-22",
        "noi_dung": f"""
3. Tổ chức thực hiện
{parent_content}

3.1. CPCIT
CPCIT chuẩn bị nền tảng và hướng dẫn kỹ thuật.

3.2. Các đơn vị
Các đơn vị rà soát và gửi dữ liệu.
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    body_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "document_body"]

    assert [chunk.metadata.get("section_title") for chunk in body_chunks] == [
        "3. Tổ chức thực hiện",
        "3.1. CPCIT",
        "3.2. Các đơn vị",
    ]
    assert parent_content in body_chunks[0].content
    assert "3. Tổ chức thực hiện" in body_chunks[1].content
    assert parent_content not in body_chunks[1].content


def test_doffice_article_heading_ocr_variant_is_parent_of_numbered_items() -> None:
    source = {
        "id_vb": "1479038",
        "ky_hieu": "660/QD-IT",
        "trich_yeu": "Phê duyệt kết quả lựa chọn nhà thầu",
        "noi_ban_hanh": "Công ty Công nghệ thông tin Điện lực miền Trung",
        "ngay_vb": "2025-08-25",
        "noi_dung": """
Ðiều 1 . Phê duyệt kết quả lựa chọn nhà thầu gói thầu: 06.PTV: Cung cấp dịch vụ bản đồ nền thuộc Chương trình: Cung cấp dịch vụ bản đồ nền, bao gồm:

1. Tên gói thầu: 06.PTV: Cung cấp dịch vụ bản đồ nền.

2. Giá gói thầu: 615.752.500 VNĐ.
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    body_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "document_body"]

    assert [chunk.metadata.get("section_title") for chunk in body_chunks] == [
        "1. Tên gói thầu: 06.PTV: Cung cấp dịch vụ bản đồ nền.",
        "2. Giá gói thầu: 615.752.500 VNĐ.",
    ]
    assert body_chunks[0].metadata["heading_path"] == [
        "Điều 1 . Phê duyệt kết quả lựa chọn nhà thầu gói thầu: 06.PTV: Cung cấp dịch vụ bản đồ nền thuộc Chương trình: Cung cấp dịch vụ bản đồ nền, bao gồm:",
        "1. Tên gói thầu: 06.PTV: Cung cấp dịch vụ bản đồ nền.",
    ]
    assert "Điều 1 . Phê duyệt kết quả lựa chọn nhà thầu" in body_chunks[0].content
    assert "1. Tên gói thầu" in body_chunks[0].content
    assert "bao gồm:\n1. Tên gói thầu" in body_chunks[0].content
    assert "bao gồm:\n\n1. Tên gói thầu" not in body_chunks[0].content
    assert "Điều 1 . Phê duyệt kết quả lựa chọn nhà thầu" in body_chunks[1].content
    assert "2. Giá gói thầu" in body_chunks[1].content
    assert "bao gồm:\n2. Giá gói thầu" in body_chunks[1].content
    assert "bao gồm:\n\n2. Giá gói thầu" not in body_chunks[1].content


def test_doffice_appendix_sections_are_kept_with_child_goals() -> None:
    source = {
        "id_vb": "1479036",
        "ky_hieu": "6517/EVNCPC-VTCNTT+KD+KT",
        "trich_yeu": "Kế hoạch mô tả dữ liệu GIS hạ thế",
        "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
        "ngay_vb": "2025-08-23",
        "noi_dung": """
Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO VÀ CHUYỂN ĐỔI TỪ CÁC HỆ THỐNG, PHẦN MỀM HIỆN CÓ CỦA EVNCPC SANG DỮ LIỆU GIS HẠ THẾ

1. Mục tiêu
- Khởi tạo khung CSDL GIS lưới điện hạ thế bao gồm 10 đối tượng thiết bị.
- Chuyển đổi dữ liệu ban đầu cho 07 đối tượng có độ ưu tiên cao.

2. Nội dung thực hiện
Các đơn vị phối hợp rà soát dữ liệu.
""",
    }

    normalized = normalize_doffice_source(source)
    chunks = build_doffice_chunks(normalized)
    body_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "document_body"]
    goal_chunk = next(chunk for chunk in body_chunks if chunk.metadata.get("section_title") == "1. Mục tiêu")

    assert "Phụ lục 02" in goal_chunk.content
    assert "MÔ TẢ DỮ LIỆU KHỞI TẠO" in goal_chunk.content
    assert "1. Mục tiêu" in goal_chunk.content
    assert "Khởi tạo khung CSDL GIS lưới điện hạ thế" in goal_chunk.content
    assert goal_chunk.metadata["heading_path"] == ["Phụ lục 02", "1. Mục tiêu"]


def test_doffice_table_context_keeps_appendix_and_goal_context() -> None:
    normalized = normalize_doffice_source(
        {
            "id_vb": "1479037",
            "ky_hieu": "6518/EVNCPC-VTCNTT+KD+KT",
            "trich_yeu": "Kế hoạch bảng trong phụ lục GIS",
            "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
            "ngay_vb": "2025-08-24",
            "noi_dung": """
Phụ lục 02
MÔ TẢ DỮ LIỆU KHỞI TẠO VÀ CHUYỂN ĐỔI TỪ CÁC HỆ THỐNG, PHẦN MỀM HIỆN CÓ CỦA EVNCPC SANG DỮ LIỆU GIS HẠ THẾ

1. Mục tiêu
- Khởi tạo khung CSDL GIS lưới điện hạ thế bao gồm 10 đối tượng thiết bị.
- Chuyển đổi dữ liệu ban đầu cho 07 đối tượng có độ ưu tiên cao, bao gồm:
(1) F08_CotDien_HT – Lớp cột điện;
(2) F09_DuongDay-HT – Lớp đường dây;
(3) F05_CongToKhachHang-HT – Lớp công tơ khách hàng;
(4) F01_DiemDo-HT – Lớp điểm đo;
(5) F03_ThietBiDoDem-HT – Lớp thiết bị đo đếm;
(6) F02_ThietBiDongCat_HT – Lớp thiết bị đóng cắt;
(7) F10_TuPhanPhoi_HT – Lớp tủ phân phối.
Các lớp dữ liệu GIS còn lại (03 lớp), sẽ xem xét chuyển đổi ở giai đoạn sau.

| TT | Tên trường | Mô tả |
| --- | --- | --- |
| 1 | ID | Mã định danh |
""",
        }
    )

    table = normalized.tables[0]
    table_context = table.metadata["table_context"]
    assert "Phụ lục 02" in table_context
    assert "MÔ TẢ DỮ LIỆU KHỞI TẠO" in table_context
    assert "1. Mục tiêu" in table_context
    assert "(1) F08_CotDien_HT" in table_context
    assert "(7) F10_TuPhanPhoi_HT" in table_context

    chunks = build_doffice_chunks(normalized)
    table_parent = next(chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_parent")
    assert "Phụ lục 02" in table_parent.content
    assert "(1) F08_CotDien_HT" in table_parent.content


def test_doffice_markdown_tables_are_normalized_and_chunked() -> None:
    normalized = normalize_doffice_source(
        {
            "id_vb": "1068586",
            "ky_hieu": "123/CPCIT",
            "trich_yeu": "Kiem tra markdown table",
            "noi_ban_hanh": "CPCIT",
            "nguoi_ky": "Nguyen Van A",
            "ten_file": "markdown-table.md",
            "duong_dan": "/doffice/markdown-table.md",
            "ngay_vb": "2026-06-10",
            "nam": 2026,
            "thang": 6,
            "tom_tat": "Tai lieu co bang markdown.",
            "noi_dung": """
--- Page 1 ---
# PHU LUC
Can cu van ban so 123/CPCIT.
Bang danh muc truong du lieu GIS

| TT | Ten truong | Mo ta | Kieu du lieu |
| --- | --- | --- | --- |
| 1 | ID | Ma dinh danh | String |
| 2 | MaTram | Ma tram bien ap | String |

Noi nhan:
KT. GIAM DOC
""",
        }
    )

    assert len(normalized.tables) == 1
    assert normalized.tables[0].metadata["source_format"] == "markdown_table"
    assert normalized.tables[0].metadata["table_name"] == "Bang danh muc truong du lieu GIS"
    assert normalized.tables[0].metadata["table_context"]
    assert "|" not in normalized.clean_text
    assert "PHU LUC" in normalized.clean_text
    assert "Can cu van ban" in normalized.clean_text

    chunks = build_doffice_chunks(normalized)
    chunk_types = [chunk.metadata["chunk_type"] for chunk in chunks]
    assert "table_parent" in chunk_types
    assert chunk_types.count("table_row") == 2
    assert "table_group" in chunk_types
    assert chunk_types.count("table_column") == 4

    first_row = next(chunk for chunk in chunks if chunk.metadata.get("row_number") == "1")
    assert first_row.metadata["row_data"]["Ten truong"] == "ID"
    assert first_row.metadata["field_name"] == "ID"
    assert first_row.metadata["table_context"]
    assert first_row.metadata["source_format"] == "markdown_table"

    group = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "table_group")
    assert group.metadata["group_name"] == "Rows 1-2"
    assert group.metadata["row_start"] == 1
    assert group.metadata["row_end"] == 2
    assert "Các dòng trong nhóm (Markdown table):" in group.content
    assert "| TT | Ten truong | Mo ta | Kieu du lieu |" in group.content
    assert "Bảng DOffice" not in group.content

    mo_ta_column = next(
        chunk
        for chunk in chunks
        if chunk.metadata.get("chunk_type") == "table_column"
        and chunk.metadata.get("column_name") == "Mo ta"
    )
    assert "Ten truong: ID" in mo_ta_column.content
    assert "Ma dinh danh" in mo_ta_column.content


def test_doffice_table_fallback_name_does_not_expose_internal_source_label() -> None:
    normalized = normalize_doffice_source(
        {
            "id_vb": "1068589",
            "ky_hieu": "126/CPCIT",
            "trich_yeu": "Kiem tra ten bang fallback",
            "noi_ban_hanh": "CPCIT",
            "ngay_vb": "2026-06-12",
            "noi_dung": """
Noi dung truoc bang khong co tieu de.

| TT | Noi dung |
| --- | --- |
| 1 | Kiem tra |
""",
        }
    )
    chunks = build_doffice_chunks(normalized)
    table_parent = next(chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_parent")

    assert normalized.tables[0].metadata["table_name"] == "Bảng 1"
    assert "Bảng: Bảng 1" in table_parent.content
    assert "Bảng DOffice" not in table_parent.content


def test_doffice_comparison_tables_add_column_chunks() -> None:
    normalized = normalize_doffice_source(
        {
            "id_vb": "1068587",
            "ky_hieu": "124/CPCIT",
            "trich_yeu": "Kiem tra bang so sanh",
            "noi_ban_hanh": "CPCIT",
            "nguoi_ky": "Nguyen Van A",
            "ten_file": "comparison-table.md",
            "duong_dan": "/doffice/comparison-table.md",
            "ngay_vb": "2026-06-11",
            "nam": 2026,
            "thang": 6,
            "tom_tat": "Tai lieu co bang so sanh.",
            "noi_dung": """
Phu luc phan cong xu ly du lieu

| TT | Du lieu | CPCIT | Cac CTDL |
| --- | --- | --- | --- |
| 1 | GIS 110kV | Chuan bi du lieu, chuyen doi cau truc va gui CSDL offline | Kiem tra, hieu chinh du lieu va gui lai ket qua |
| 2 | GIS trung the | Lap tai lieu huong dan va ho tro sap nhap du lieu | Sap nhap du lieu theo huong dan va kiem tra CSDL |
""",
        }
    )

    chunks = build_doffice_chunks(normalized)
    column_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_column"]
    assert {chunk.metadata.get("column_name") for chunk in column_chunks} == {"TT", "Du lieu", "CPCIT", "Cac CTDL"}
    assert all(chunk.metadata.get("is_table_column") is True for chunk in column_chunks)
    assert all(chunk.metadata.get("column_value_count") == 2 for chunk in column_chunks)

    cpcit_column = next(chunk for chunk in column_chunks if chunk.metadata.get("column_name") == "CPCIT")
    assert "CPCIT" in cpcit_column.content
    assert "| Dòng | Ngữ cảnh hàng | Nội dung cột |" in cpcit_column.content
    assert "Du lieu: GIS 110kV" in cpcit_column.content
    assert "Chuan bi du lieu" in cpcit_column.content
    assert "Kiem tra, hieu chinh" not in cpcit_column.content

    row_chunks = [chunk for chunk in chunks if chunk.metadata.get("chunk_type") == "table_row"]
    assert len(row_chunks) == 2


def test_doffice_html_tables_keep_rowspan_context_and_chunk_rows() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_realistic_tables())

    assert normalized.issued_date == "04/06/2026"
    assert "|" not in normalized.clean_text
    assert len(normalized.tables) == 2
    assert len(normalized.tables[0].rows) == 34
    assert len(normalized.tables[1].rows) == 9
    assert len(normalized.table_rows) == 43

    rows = {row.metadata["row_number"]: row for row in normalized.tables[0].rows}
    assert rows["2"].metadata["feature_name"] == "LiÃªn ket khach hang"
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
    assert any(chunk.metadata.get("chunk_type") == "table_row" and chunk.metadata.get("feature_name") == "Dashboard" and chunk.content.count("- Y ") == 4 for chunk in chunks)
    assert any(chunk.metadata.get("chunk_type") == "table_row" and chunk.metadata.get("feature_name") == "Cap dien moi ha ap" and chunk.metadata.get("phase") == "Giai doan 2" for chunk in chunks)
    assert any(chunk.metadata.get("chunk_type") == "table_group" and chunk.metadata.get("group_name") == "App cham soc khach hang - Giai doan 1" for chunk in chunks)
    assert any(chunk.metadata.get("chunk_type") == "table_group" and chunk.metadata.get("group_name") == "App cham soc khach hang - Giai doan 2" for chunk in chunks)
    assert any(chunk.metadata.get("chunk_type") == "table_group" and chunk.metadata.get("platform") == "Website Quan tri noi dung (CMS)" for chunk in chunks)


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


def test_ingest_doffice_document_skips_existing_without_force_refresh() -> None:
    existing = SimpleNamespace(
        id=EXISTING_DOCUMENT_ID,
        organization_id=ORG_ID,
        document_metadata={"id_vb": "1068586", "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT"},
    )
    source = FakeSource()
    repository = FakeRepository(existing=existing)
    service, vector_store = _ingestion_service(repository, source)

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
    assert vector_store.deleted == []


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
