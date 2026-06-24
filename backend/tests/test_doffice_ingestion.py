from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_log_repository, get_doffice_ingestion_service
from app.main import app
from app.schemas.documents import ChunkPreview, DocumentChunkResponse, DocumentVectorIndexResponse, DofficeIngestResponse
from app.services.document_sources import DOFFICE_SOURCE_TYPE, DofficeDocument, DofficeElasticsearchSource
from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
from app.services.ingestion.ingestion_doffice_content_normalizer import apply_spacing_fixes, normalize_doffice_source
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
  <tr><th>TT</th><th>TrÆ°á»ng dá»¯ liá»‡u</th><th>Nguá»“n dá»¯ liá»‡u</th><th>Chuyá»ƒn Ä‘á»•i sang GIS</th></tr>
  <tr><td>1</td><td><strong>MaTramBienAp</strong></td><td>CPCIT</td><td>GIS 110kV</td></tr>
</table>
<br>
- - NhÆ° trÃªn;
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
    assert "Chuyá»ƒn Ä‘á»•i sang GIS" in cleaned
    assert "CPCIT" in cleaned
    assert "GIS 110kV" in cleaned
    assert "- NhÆ° trÃªn;" in cleaned


def test_vietnamese_ocr_spacing_cleaner_repairs_common_splits() -> None:
    cases = {
        "Li ÃƒÂªn kÃ¡ÂºÂ¿t khÃƒÂ¡ch hÃƒÂ ng": "LiÃƒÂªn kÃ¡ÂºÂ¿t khÃƒÂ¡ch hÃƒÂ ng",
        "TÃƒÂ  i khoÃ¡ÂºÂ£n": "TÃƒÂ i khoÃ¡ÂºÂ£n",
        "Th anh toÃƒÂ¡n": "Thanh toÃƒÂ¡n",
        "H ÃƒÂ³a Ã„â€˜Ã†Â¡n": "HÃƒÂ³a Ã„â€˜Ã†Â¡n",
        "D anh mÃ¡Â»Â¥c Ã„â€˜iÃ¡Â»Æ’m thu": "Danh mÃ¡Â»Â¥c Ã„â€˜iÃ¡Â»Æ’m thu",
        "Q uÃ¡ÂºÂ£n lÃƒÂ½ ngÃ†Â°Ã¡Â»Âi dÃƒÂ¹ng": "QuÃ¡ÂºÂ£n lÃƒÂ½ ngÃ†Â°Ã¡Â»Âi dÃƒÂ¹ng",
        "D ashboard": "Dashboard",
        "bÃƒÂ¡oc ÃƒÂ¡o": "bÃƒÂ¡o cÃƒÂ¡o",
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
                                "trich_yeu": "Triá»ƒn khai GIS",
                                "noi_ban_hanh": "EVNCPC",
                                "nguoi_ky": "Nguyen Van A",
                                "ten_file": "6515.pdf",
                                "duong_dan": "/doffice/6515.pdf",
                                "noi_dung": "--- Page 1 ---\n## CPCIT\nNá»™i dung vÄƒn báº£n",
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

    monkeypatch.setattr("app.services.document_sources.document_source_doffice_elasticsearch_source.httpx.AsyncClient", FakeAsyncClient)

    document = asyncio.run(
        DofficeElasticsearchSource(url="http://example.test/_search", timeout_seconds=5).fetch_document_by_id_vb("1068586")
    )

    assert requests[0]["method"] == "GET"
    assert document.id_vb == "1068586"
    assert document.ky_hieu == "6515/EVNCPC-VTCNTT+KD+KT"
    assert document.trich_yeu == "Triá»ƒn khai GIS"
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
            trich_yeu="Giao nhiá»‡m vá»¥ GIS cho CPCIT",
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
            raw_noi_dung="--- Page 1 ---\n## CPCIT\nCPCIT thá»±c hiá»‡n GIS 110kV.",
            clean_text="CPCIT\nCPCIT thá»±c hiá»‡n GIS 110kV.",
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
      <td>Li ÃƒÂªn ket khach hang</td><td rowspan="2">Cap nhat giao dien moi</td>
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
        "Li ÃƒÂªn ket khach hang",
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
        (32, "Bao cao", "Hieu chinh bÃƒÂ¡oc ÃƒÂ¡o"),
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
    lien_ket = rows["LiÃƒÂªn ket khach hang"]
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
    assert "TÃ³m táº¯t nguá»“n" not in body.content
    assert "PHU LUC" not in body.content
    assert "[[TABLE_1]]" not in body.content

    table_parent = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "table_parent")
    assert table_parent.metadata["row_count"] == 4
    assert "Báº£ng:" in table_parent.content
    assert "Sá»‘ dÃ²ng: 4" in table_parent.content
    assert "CÃ¡c cá»™t chuáº©n hÃ³a:" in table_parent.content
    assert "chá»©c nÄƒng/mÃ n hÃ¬nh" in table_parent.content
    assert "ná»™i dung hiá»‡u chá»‰nh/bá»• sung" in table_parent.content
    assert "NhÃ³m chÃ­nh:" in table_parent.content
    assert not any(marker in table_parent.content for marker in ("BÃ¡Âº", "SÃ¡Â»", "CÃƒ", "NhÃƒ", "dÃƒÂ²ng", "cÃ¡Â»"))
    assert "Cap dien moi ha ap" not in table_parent.content
    assert table_parent.metadata["table_title"] == table_parent.metadata["table_name"]
    assert table_parent.metadata["table_headers"]

    cap_dien = next(chunk for chunk in chunks if chunk.metadata.get("feature_name") == "Cap dien moi ha ap")
    assert cap_dien.metadata["platform"] == "App cham soc khach hang"
    assert cap_dien.metadata["change_content"] == "Bo sung chuc nang moi"
    assert cap_dien.metadata["phase"] == "Giai doan 2"
    assert cap_dien.metadata["table_title"] == cap_dien.metadata["table_name"]
    assert cap_dien.metadata["table_headers"]
    assert cap_dien.metadata["source_type"] == DOFFICE_SOURCE_TYPE
    assert cap_dien.metadata["id_vb"] == "1459570"
    assert cap_dien.metadata["document_code"] == "907/EVNICT-TTPM"
    assert cap_dien.metadata["trich_yeu"] == "Cap nhat ung dung EVN CSKH"
    assert cap_dien.metadata["is_table_row"] is True
    assert cap_dien.metadata["indexable"] is True

    footer = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "footer_signature")
    assert "PHU LUC" not in footer.content
    assert "[[TABLE_1]]" not in footer.content
    assert footer.metadata["is_footer_or_signature"] is True
    assert footer.metadata["indexable"] is False
    assert footer.metadata["embedding_enabled"] is False

def test_doffice_summary_filters_pii_and_table_rows_are_lookup_ready() -> None:
    source = {
        "id_vb": "608",
        "ky_hieu": "608/QÄ-IT",
        "trich_yeu": "Quyáº¿t Ä‘á»‹nh cá»­ cÃ¡n bá»™ tham gia khÃ³a Ä‘Ã o táº¡o á»¨ng dá»¥ng Python trÃªn ná»n táº£ng ArcGIS",
        "noi_ban_hanh": "CPCIT",
        "ngay_vb": "2026-06-10",
        "tom_tat": "Quyáº¿t Ä‘á»‹nh cá»­ 06 cÃ¡n bá»™ Ä‘i Ä‘Ã o táº¡o. Nguyá»…n Thanh PhÃº 0983129374 phunt3@cpc.vn; danh sÃ¡ch cÃ¡n bá»™ PM kÃ¨m theo.",
        "noi_dung": """
Äiá»u 1. Cá»­ cÃ¡n bá»™ tham gia khÃ³a Ä‘Ã o táº¡o á»¨ng dá»¥ng Python trÃªn ná»n táº£ng ArcGIS.
Thá»i gian Ä‘Ã o táº¡o tá»« ngÃ y 17/06/2026 Ä‘áº¿n ngÃ y 19/06/2026.
Äá»‹a Ä‘iá»ƒm Ä‘Ã o táº¡o: HÃ  Ná»™i.
ÄÆ¡n vá»‹ Ä‘Ã o táº¡o: ESRI Viá»‡t Nam.
Kinh phÃ­ do CPCIT chi tráº£.
Danh sÃ¡ch cÃ¡n bá»™ tham gia
<table>
<tr><th>STT</th><th>Há» tÃªn</th><th>Chá»©c vá»¥</th><th>PhÃ²ng</th><th>Äiá»‡n thoáº¡i</th><th>Email</th></tr>
<tr><td>1</td><td>Nguyá»…n Thanh PhÃº</td><td>ChuyÃªn viÃªn</td><td>VH</td><td>0983129374</td><td>phunt3@cpc.vn</td></tr>
<tr><td>2</td><td>Tráº§n VÄƒn B</td><td>ChuyÃªn viÃªn</td><td>PM</td><td>0912345678</td><td>b@cpc.vn</td></tr>
</table>
""",
    }

    chunks = build_doffice_chunks(normalize_doffice_source(source))
    summary = next(chunk for chunk in chunks if chunk.metadata["chunk_type"] == "document_summary")
    row = next(chunk for chunk in chunks if chunk.metadata.get("person_name") == "Nguyá»…n Thanh PhÃº")

    assert "0983129374" not in summary.content
    assert "phunt3@cpc.vn" not in summary.content
    assert "Nguyá»…n Thanh PhÃº" not in summary.content
    assert len(summary.content.split()) <= 200
    for chunk in chunks:
        if chunk.metadata.get("chunk_type") in {"document_summary", "document_header", "legal_clause", "table_parent", "table_row"}:
            assert chunk.metadata["source_span"]["start"] <= chunk.metadata["source_span"]["end"]

    assert row.metadata["row_key"] == "Nguyá»…n Thanh PhÃº"
    assert row.metadata["position"] == "ChuyÃªn viÃªn"
    assert row.metadata["department"] == "VH"
    assert row.metadata["phone"] == "0983129374"
    assert row.metadata["email"] == "phunt3@cpc.vn"
    assert "Há» tÃªn: Nguyá»…n Thanh PhÃº" in row.content
    assert "PhÃ²ng/ÄÆ¡n vá»‹: VH" in row.content
def test_doffice_html_tables_keep_rowspan_context_and_chunk_rows() -> None:
    normalized = normalize_doffice_source(_sample_doffice_source_with_realistic_tables())

    assert normalized.issued_date == "04/06/2026"
    assert "|" not in normalized.clean_text
    assert len(normalized.tables) == 2
    assert len(normalized.tables[0].rows) == 34
    assert len(normalized.tables[1].rows) == 9
    assert len(normalized.table_rows) == 43

    rows = {row.metadata["row_number"]: row for row in normalized.tables[0].rows}
    assert rows["2"].metadata["feature_name"] == "LiÃƒÂªn ket khach hang"
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
    assert not repository.document.parsed_text.startswith("TÃ³m táº¯t nguá»“n:")
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
                trich_yeu="Giao nhiá»‡m vá»¥ GIS",
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
