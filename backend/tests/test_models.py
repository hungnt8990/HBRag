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
        "user_memories",
        "session_summaries",
    }


def test_chunk_model_contains_keyword_search_vector() -> None:
    columns = Chunk.__table__.columns
    index_names = {index.name for index in Chunk.__table__.indexes}

    assert "search_vector" in columns
    assert columns["search_vector"].nullable is True
    assert "ix_chunks_search_vector" in index_names
