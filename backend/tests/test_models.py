from sqlalchemy.orm import configure_mappers

from app.db.base import Base
from app.models import (
    ChatMessage,
    ChatSession,
    Chunk,
    Citation,
    Document,
    DocumentAccessLog,
    DocumentFile,
    DocumentPipelineLog,
    GraphDocumentStatus,
    GraphExtractionLog,
    IngestionProfileConfig,
    KnowledgeBase,
    KnowledgeBaseMember,
    Organization,
    RetrievalLog,
    Role,
    User,
)


def test_database_models_are_importable() -> None:
    configure_mappers()

    models = [
        Document,
        DocumentFile,
        Organization,
        User,
        Role,
        DocumentPipelineLog,
        DocumentAccessLog,
        GraphExtractionLog,
        GraphDocumentStatus,
        KnowledgeBase,
        KnowledgeBaseMember,
        IngestionProfileConfig,
        Chunk,
        ChatSession,
        ChatMessage,
        Citation,
        RetrievalLog,
    ]

    assert {model.__tablename__ for model in models} == {
        "documents",
        "document_files",
        "organizations",
        "users",
        "roles",
        "document_pipeline_logs",
        "document_access_logs",
        "graph_extraction_logs",
        "graph_document_status",
        "knowledge_bases",
        "knowledge_base_members",
        "ingestion_profile_configs",
        "chunks",
        "chat_sessions",
        "chat_messages",
        "citations",
        "retrieval_logs",
    }


def test_model_metadata_contains_initial_tables() -> None:
    assert set(Base.metadata.tables) == {
        "documents",
        "document_files",
        "doffice_raw_documents",
        "chunks",
        "chat_sessions",
        "chat_messages",
        "citations",
        "retrieval_logs",
        "organizations",
        "users",
        "roles",
        "user_roles",
        "document_pipeline_logs",
        "document_access_logs",
        "graph_extraction_logs",
        "graph_document_status",
        "knowledge_bases",
        "knowledge_base_members",
        "knowledge_artifacts",
        "ingestion_profile_configs",
        "rag_runtime_configs",
        "user_memories",
        "session_summaries",
        "dm_don_vi",
        "dm_phong_ban",
        "dm_nhan_vien",
    }


def test_chunk_model_contains_keyword_search_vector() -> None:
    columns = Chunk.__table__.columns
    index_names = {index.name for index in Chunk.__table__.indexes}

    assert "enriched_content" in columns
    assert columns["enriched_content"].nullable is True
    assert "search_vector" in columns
    assert columns["search_vector"].nullable is True
    assert "ix_chunks_search_vector" in index_names


def test_graph_document_status_model_matches_expected_table() -> None:
    columns = GraphDocumentStatus.__table__.columns
    index_names = {index.name for index in GraphDocumentStatus.__table__.indexes}

    assert GraphDocumentStatus.__tablename__ == "graph_document_status"
    assert GraphExtractionLog.__tablename__ == "graph_extraction_logs"
    assert columns["document_id"].nullable is False
    assert columns["document_id"].unique is True
    assert columns["graph_indexed"].nullable is False
    assert columns["chunks_processed"].nullable is False
    assert "ix_graph_document_status_document_id" in index_names
