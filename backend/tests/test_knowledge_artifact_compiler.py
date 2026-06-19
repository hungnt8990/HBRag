from __future__ import annotations

import inspect
from uuid import UUID, uuid4

from app.models.chunk import Chunk
from app.models.document import Document
from app.services.knowledge_artifact_compiler import KnowledgeArtifactCompiler

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_compiler_creates_identifier_and_person_assignment_artifacts() -> None:
    document = Document(
        id=DOCUMENT_ID,
        title="Ke hoach AI",
        source_type="pdf",
        status="chunked",
        document_metadata={"document_number": "3113/EVN-KDMBD"},
    )
    chunks = [
        Chunk(
            id=uuid4(),
            document_id=DOCUMENT_ID,
            chunk_index=0,
            content="So hieu 3113/EVN-KDMBD ngay 2024-01-20 ve ke hoach AI.",
            chunk_metadata={"chunk_type": "text", "page_number": 1},
        ),
        *_assignment_chunks(),
    ]

    artifacts = KnowledgeArtifactCompiler().compile_document(document=document, chunks=chunks)

    identifier_artifacts = [artifact for artifact in artifacts if artifact.artifact_type == "identifier_lookup"]
    assert any("3113/EVN-KDMBD" in artifact.normalized_identifiers.get("identifiers", []) for artifact in identifier_artifacts)

    person_artifacts = [artifact for artifact in artifacts if artifact.artifact_type == "person_assignment_artifact"]
    assert len(person_artifacts) == 4
    task_areas = {
        assignment.get("task_area")
        for artifact in person_artifacts
        for assignment in artifact.structured_data["assignments"]
    }
    assert task_areas == {"RAG", "OCR", "Kho du lieu AI", "Platform AI"}
    assert all(artifact.citation_map["chunks"] for artifact in person_artifacts)


def test_compiler_creates_policy_rule_from_structured_row_without_neighbor_bleed() -> None:
    document = Document(
        id=DOCUMENT_ID,
        title="Thoa uoc lao dong tap the",
        source_type="pdf",
        status="chunked",
        document_metadata={},
    )
    chunk = Chunk(
        id=uuid4(),
        document_id=DOCUMENT_ID,
        chunk_index=0,
        content="Con de ket hon: NLĐ duoc nghi viec rieng co huong luong 01 ngay.",
        chunk_metadata={
            "chunk_type": "structured_fact_row",
            "relationship_type": "leave_benefit",
            "case_name": "Con de ket hon",
            "beneficiary": "NLD",
            "days": "01 ngay",
        },
    )

    artifacts = KnowledgeArtifactCompiler().compile_document(document=document, chunks=[chunk])

    policy_artifacts = [artifact for artifact in artifacts if artifact.artifact_type == "policy_rule_artifact"]
    assert len(policy_artifacts) == 1
    assert policy_artifacts[0].structured_data["days"] == "01 ngay"
    assert "numeric_facts" in policy_artifacts[0].structured_data
    assert policy_artifacts[0].source_chunk_ids == [str(chunk.id)]


def test_compiler_source_does_not_hardcode_sample_entities() -> None:
    source = inspect.getsource(KnowledgeArtifactCompiler)
    assert "Nguyen Quang Lam" not in source
    assert "3113/EVN-KDMBD" not in source
    assert "Con de ket hon" not in source


def _assignment_chunks() -> list[Chunk]:
    rows = [
        (3, "RAG", "PTUD"),
        (4, "OCR", "PM"),
        (5, "Kho du lieu AI", "VH"),
        (6, "Platform AI", "PTUD/PoC ThinkLabs"),
    ]
    return [
        Chunk(
            id=uuid4(),
            document_id=DOCUMENT_ID,
            chunk_index=index,
            content=f"STT {stt}: Nguyen Quang Lam - {task_area} - {department}",
            chunk_metadata={
                "chunk_type": "table_row",
                "stt": stt,
                "person_name": "Nguyen Quang Lam",
                "task_area": task_area,
                "department": department,
                "table_id": "staff-matrix",
                "row_start": stt,
            },
        )
        for index, (stt, task_area, department) in enumerate(rows, start=1)
    ]

