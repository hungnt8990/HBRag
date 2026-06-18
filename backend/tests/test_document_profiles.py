import asyncio
import copy
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.api.routes.chat import _resolve_profile_settings
from app.api.routes.documents import get_document_repository
from app.main import app
from app.repositories.documents import ChunkCreate
from app.services.document_profiles import (
    FALLBACK_CONFIG,
    PROFILE_CONFIGS,
    detect_profile,
    profile_config,
    resolve_profile,
    resolve_profile_with_evidence,
)
from app.services.ingestion_queue import IngestionQueue

DOCUMENT_ID = UUID("55555555-5555-5555-5555-555555555555")

LEGAL_TEXT = (
    "CHƯƠNG I QUY ĐỊNH CHUNG\n"
    "Điều 1. Phạm vi điều chỉnh\nNội dung điều 1.\n\n"
    "Điều 2. Đối tượng áp dụng\nNội dung điều 2.\n\n"
    "Điều 3. Giải thích từ ngữ\nNội dung điều 3.\n"
)
GENERAL_TEXT = (
    "This is a general process document describing team workflows and operating notes."
)
SPREADSHEET_TEXT = "\n".join(
    f"Column A {index} | Column B {index} | Column C {index}" for index in range(20)
)
SERIALIZED_TABLE_TEXT = (
    "TABLE_ROW table_id=docx_t1 row=1 | cell_1: Alice | cell_2: Platform\n"
    "TABLE_ROW table_id=docx_t1 row=2 | cell_1: Bob | cell_2: QA\n"
)

CATALOG_TEXT = (
    "DANH MỤC CÁC NGÔN NGỮ LẬP TRÌNH, PLATFORM, FRAMEWORK, "
    "CÔNG NGHỆ DÙNG CHUNG TRONG HỆ THỐNG PHẦN MỀM CỦA EVN\n"
    "TT\nThành phần công nghệ/Công cụ sử dụng\n"
    "Hãng sản xuất/Nhà cung cấp\nMục đích sử dụng\n"
    "1 Version control\n"
    "1.1 Git - Azure Repos Microsoft Quản lý mã nguồn các dự án phát triển phần mềm\n"
    "2 Message Queue\n"
    "2.1 RabitMQ Vmware Inc Được sử dụng như một ứng dụng trung chuyển tin nhắn\n"
)

STAFF_TECH_MATRIX_TEXT = (
    "NHIỆM VỤ CÁC MẢNG CÔNG NGHỆ NỀN TẢNG AI\n"
    "DANH SÁCH NHÂN SỰ PHỤ TRÁCH TỪNG MẢNG CÔNG NGHỆ LÕI\n"
    "STT | Mảng công nghệ | Phòng chủ trì | Nhân sự đề xuất | Mục tiêu\n"
    "1 | RAG trên dữ liệu nội bộ | Ban Công nghệ thông tin | "
    "Nguyễn Trọng Hùng, Phước Lâm | Xây dựng nền tảng hỏi đáp trên dữ liệu nội bộ\n"
)


class FakeDocumentRepository:
    def __init__(self, *, parsed_text: str, document_profile: str = "auto") -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            status="parsed",
            parsed_text=parsed_text,
            document_profile=document_profile,
        )
        self.created_chunks: list[ChunkCreate] = []
        self.metadata_updates: dict[str, object] = {}
        self.committed = False

    async def get_document(self, document_id: UUID):
        if document_id != DOCUMENT_ID:
            return None
        return self.document

    async def delete_chunks_for_document(self, document_id: UUID) -> None:
        return None

    async def create_chunks(self, *, document_id: UUID, chunks: list[ChunkCreate]):
        self.created_chunks = list(chunks)
        return []

    async def update_document_status(self, document, status: str):
        document.status = status
        return document

    async def update_document_metadata(self, document, metadata: dict[str, object]):
        self.metadata_updates.update(metadata)
        document.document_metadata = dict(self.metadata_updates)
        return document

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None

class FakeIngestionProfileRepository:
    def __init__(self) -> None:
        self.configs: dict[str, dict[str, object]] = {}
        self.committed = False
        self.rolled_back = False

    async def list_profile_configs(self) -> dict[str, dict[str, object]]:
        return copy.deepcopy(self.configs)

    async def seed_missing_profile_configs(
        self,
        configs: dict[str, dict[str, object]],
    ) -> None:
        for name, config in configs.items():
            self.configs.setdefault(name, copy.deepcopy(config))

    async def upsert_profile_config(
        self,
        profile_name: str,
        config: dict[str, object],
    ):
        self.configs[profile_name] = copy.deepcopy(config)
        return SimpleNamespace(profile_name=profile_name, config=config)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def test_detect_profile_legal_admin() -> None:
    assert detect_profile(LEGAL_TEXT) == "legal_admin"


def test_detect_profile_general_and_spreadsheet() -> None:
    assert detect_profile(GENERAL_TEXT) == "general"
    assert detect_profile(SPREADSHEET_TEXT) == "spreadsheet"
    assert detect_profile(SERIALIZED_TABLE_TEXT) == "spreadsheet"


def test_detect_profile_catalog_table() -> None:
    assert detect_profile(CATALOG_TEXT) == "catalog_table"

def test_detect_profile_staff_technology_matrix() -> None:
    assert detect_profile(STAFF_TECH_MATRIX_TEXT) == "staff_technology_matrix"

    detection = resolve_profile_with_evidence("auto", text=STAFF_TECH_MATRIX_TEXT)

    assert detection["profile"] == "staff_technology_matrix"
    assert detection["mode"] == "configured_rules"
    assert detection["score"] >= 6
    assert detection["evidence"]


def test_resolve_profile_auto_uses_detection() -> None:
    assert resolve_profile("auto", text=LEGAL_TEXT) == "legal_admin"
    assert resolve_profile("catalog_table", text=LEGAL_TEXT) == "catalog_table"
    assert resolve_profile("unknown", text=LEGAL_TEXT) == "general"


def test_profile_config_returns_fallback_for_unknown() -> None:
    assert profile_config("legal_admin") == PROFILE_CONFIGS["legal_admin"]
    assert profile_config(None) == FALLBACK_CONFIG


def test_auto_profile_uses_legal_article_for_legal_document() -> None:
    repository = FakeDocumentRepository(parsed_text=LEGAL_TEXT, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "legal_admin"
    assert repository.created_chunks
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "legal_article"
        assert chunk.metadata["document_profile"] == "legal_admin"


def test_auto_profile_uses_recursive_for_general_document() -> None:
    repository = FakeDocumentRepository(parsed_text="a" * 2000, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "general"
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "recursive"
        assert chunk.metadata["document_profile"] == "general"


def test_explicit_profile_overrides_detection() -> None:
    repository = FakeDocumentRepository(parsed_text="a" * 2000, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"profile": "legal_admin"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "legal_admin"
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "legal_article"


def test_admin_profiles_endpoint_returns_configs() -> None:
    repository = FakeIngestionProfileRepository()
    app.dependency_overrides[admin_routes.get_ingestion_profile_repository] = (
        lambda: repository
    )
    client = TestClient(app)
    try:
        response = client.get("/api/admin/profiles")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_profile"] == "auto"
    assert "legal_admin" in payload["profiles"]
    assert payload["configs"]["legal_admin"]["chunk_mode"] == "legal_article"
    assert payload["configs"]["legal_admin"]["answer_style"] == "policy_explainer"
    assert payload["configs"]["catalog_table"]["chunk_mode"] == "table_aware"
    assert payload["configs"]["catalog_table"]["answer_style"] == "table_qa"
    assert payload["configs"]["staff_technology_matrix"]["chunk_mode"] == "table_aware"
    assert payload["configs"]["staff_technology_matrix"]["answer_style"] == "table_qa"
    assert payload["configs"]["general"]["chunk_mode"] == "recursive"
    assert payload["configs"]["general"]["answer_style"] == "detailed"
    assert payload["configs"]["general"]["query_intent_rules"]["field_detail_schema"][
        "direct_terms"
    ]
    assert "embedding_enrichment_enabled" not in payload["configs"]["general"]
    assert "retrieval_enrichment_enabled" not in payload["configs"]["general"]
    assert "enrichment_force_on_reingest" not in payload["configs"]["general"]
    assert "enrichment_update_keyword_search_vector" not in payload["configs"]["general"]
    assert "chunk_enrichment_model" not in payload["configs"]["general"]
    assert "embedding_enrichment_model" not in payload["configs"]["general"]
    assert "reingest_enrichment_model" not in payload["configs"]["general"]
    assert payload["configs"]["spreadsheet"]["chunk_mode"] == "table_aware"
    assert payload["configs"]["spreadsheet"]["answer_style"] == "table_qa"
    assert repository.committed is True

def test_admin_profile_update_persists_to_repository() -> None:
    repository = FakeIngestionProfileRepository()
    app.dependency_overrides[admin_routes.get_ingestion_profile_repository] = (
        lambda: repository
    )
    client = TestClient(app)
    updated_config = {
        **PROFILE_CONFIGS["general"],
        "chunk_size": 1400,
        "answer_style": "table_qa",
        "embedding_enrichment_enabled": True,
        "retrieval_enrichment_enabled": True,
        "enrichment_force_on_reingest": False,
        "enrichment_update_keyword_search_vector": True,
        "chunk_enrichment_provider": "openai_compatible",
        "chunk_enrichment_model": "legacy-enrich",
        "chunk_enrichment_max_chars": 6000,
        "chunk_enrichment_version": "legacy-v1",
        "embedding_enrichment_provider": "openai_compatible",
        "embedding_enrichment_model": "gpt-enrich",
        "embedding_enrichment_max_chars": 7000,
        "embedding_enrichment_version": "v2",
        "reingest_enrichment_provider": "fake",
        "reingest_enrichment_model": "gpt-reingest",
        "reingest_enrichment_max_chars": 9000,
        "reingest_enrichment_version": "v3",
        "query_intent_rules": {
            "field_detail_schema": {
                "direct_terms": ["schema-field"],
                "required_any_terms": [],
                "specific_item_patterns": [],
                "phrases": [],
            }
        },
    }

    try:
        response = client.put(
            "/api/admin/profiles/general",
            json={"config": updated_config},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["configs"]["general"]["chunk_size"] == 1400
    assert repository.configs["general"]["chunk_size"] == 1400
    assert repository.configs["general"]["answer_style"] == "table_qa"
    assert "embedding_enrichment_enabled" not in repository.configs["general"]
    assert "retrieval_enrichment_enabled" not in repository.configs["general"]
    assert "enrichment_force_on_reingest" not in repository.configs["general"]
    assert "enrichment_update_keyword_search_vector" not in repository.configs["general"]
    assert "chunk_enrichment_provider" not in repository.configs["general"]
    assert "chunk_enrichment_model" not in repository.configs["general"]
    assert "embedding_enrichment_provider" not in repository.configs["general"]
    assert "embedding_enrichment_model" not in repository.configs["general"]
    assert "reingest_enrichment_provider" not in repository.configs["general"]
    assert "reingest_enrichment_model" not in repository.configs["general"]
    assert repository.configs["general"]["query_intent_rules"]["field_detail_schema"][
        "direct_terms"
    ] == ["schema-field"]
    assert repository.committed is True

def test_admin_profiles_merges_enrichment_defaults_for_old_config() -> None:
    repository = FakeIngestionProfileRepository()
    repository.configs["general"] = {
        "chunk_mode": "recursive",
        "chunk_size": 900,
        "retrieval_enrichment_enabled": "true",
    }
    app.dependency_overrides[admin_routes.get_ingestion_profile_repository] = (
        lambda: repository
    )
    client = TestClient(app)
    try:
        response = client.get("/api/admin/profiles")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    config = response.json()["configs"]["general"]
    assert config["chunk_size"] == 900
    assert "embedding_enrichment_enabled" not in config
    assert "retrieval_enrichment_enabled" not in config
    assert "enrichment_force_on_reingest" not in config
    assert "enrichment_update_keyword_search_vector" not in config
    assert "chunk_enrichment_model" not in config
    assert "embedding_enrichment_model" not in config
    assert "reingest_enrichment_model" not in config


def test_admin_profiles_strip_legacy_chunk_enrichment_runtime_keys() -> None:
    repository = FakeIngestionProfileRepository()
    repository.configs["general"] = {
        "chunk_mode": "recursive",
        "chunk_enrichment_provider": "openai_compatible",
        "chunk_enrichment_model": "legacy-model",
        "chunk_enrichment_max_chars": 7777,
        "chunk_enrichment_version": "legacy-v2",
    }
    app.dependency_overrides[admin_routes.get_ingestion_profile_repository] = (
        lambda: repository
    )
    client = TestClient(app)
    try:
        response = client.get("/api/admin/profiles")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    config = response.json()["configs"]["general"]
    assert "chunk_enrichment_provider" not in config
    assert "chunk_enrichment_model" not in config
    assert "chunk_enrichment_max_chars" not in config
    assert "chunk_enrichment_version" not in config


def test_chat_auto_runtime_uses_saved_document_profile() -> None:
    repository = FakeDocumentRepository(
        parsed_text=GENERAL_TEXT,
        document_profile="catalog_table",
    )

    resolved = asyncio.run(
        _resolve_profile_settings(
            repository=repository,
            profile=None,
            document_id=DOCUMENT_ID,
            top_k=None,
            candidate_k=None,
            answer_mode=None,
            answer_style=None,
            max_context_chars=None,
        )
    )

    assert resolved["profile"] == "catalog_table"
    assert resolved["answer_style"] == "table_qa"
    assert resolved["top_k"] == 12
    assert resolved["candidate_k"] == 60
    assert resolved["max_context_chars"] == 10000
    assert "field_detail_schema" in resolved["query_intent_rules"]


def test_chat_auto_runtime_detects_profile_when_saved_profile_is_auto() -> None:
    repository = FakeDocumentRepository(
        parsed_text=CATALOG_TEXT,
        document_profile="auto",
    )

    resolved = asyncio.run(
        _resolve_profile_settings(
            repository=repository,
            profile="auto",
            document_id=DOCUMENT_ID,
            top_k=None,
            candidate_k=None,
            answer_mode=None,
            answer_style=None,
            max_context_chars=None,
        )
    )

    assert resolved["profile"] == "catalog_table"
    assert resolved["answer_style"] == "table_qa"
    assert resolved["candidate_k"] == 60

def test_ingestion_profile_resolution_persists_detection_evidence() -> None:
    repository = FakeDocumentRepository(
        parsed_text=STAFF_TECH_MATRIX_TEXT,
        document_profile="auto",
    )
    queue = IngestionQueue()

    resolved = asyncio.run(
        queue._resolve_profile_for_document(
            document_id=DOCUMENT_ID,
            repository=repository,
            requested_profile="auto",
            filename="staff-matrix.md",
            content_type="text/markdown",
        )
    )

    assert resolved == "staff_technology_matrix"
    assert repository.metadata_updates["resolved_ingestion_profile"] == "staff_technology_matrix"
    assert repository.metadata_updates["profile_detection_mode"] == "auto"
    assert repository.metadata_updates["profile_detection_score"] >= 6
    assert repository.metadata_updates["profile_detection_evidence"]
    assert repository.committed is True
