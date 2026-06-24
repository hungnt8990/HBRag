from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

from docx import Document

from app.services.parsers.parser_docx_parser import DocxParser
from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService
from app.services.chunkers.chunker_table_aware_chunking import table_aware_chunk_text

DOCUMENT_ID = UUID("10000000-aaaa-4000-9000-000000000001")
OTHER_DOCUMENT_ID = UUID("10000000-aaaa-4000-9000-000000000002")
TABLE_QUERY = (
    "Danh sÃ¡ch h? gia dÌnh du?c kÃªu g?i ?ng h? kh?c ph?c thi?t h?i do con bÄƒo s? 10 "
    "g?m nh?ng ai?"
)
HOUSEHOLD_NAMES = [
    "ÄoÃ n T?n ChÃ¢u",
    "Khuong Thanh HÃ ",
    "Tr?n ÄÌnh Thanh",
    "Ä?ng Th? Hi?n",
    "Nguy?n Th? Thu Lan",
    "ÄoÃ n Ng?c BÃ­ch Th?y",
    "Hu?nh Th? Thanh",
    "LÃª Th? Äan Thanh",
    "Nguy?n Van Tu?ng",
    "Tr?n Th? Thu Trang",
    "Nguy?n T?n Trung",
    "Ki?u Phu?c Sen",
    "Nguy?n Th? Huong",
    "HoÃ ng Quang",
    "Tr?n Th? L?",
    "Tr?n Nh?t L?i",
    "LÃª Van HÃ ",
    "Tr?n LÃª Nh?t TrÃ¢m",
    "Äinh Th? ThÃºy Phuong",
    "Tr?nh Minh Quang",
    "Hu?nh Van Thu",
    "Nguy?n Th? Giang Phuong",
    "LÃª Th? Thanh",
    "NgÃ´ Anh Ä?c",
    "Ä? Th? Thu Hi?p",
    "Nguy?n Van LiÃªn",
    "Nguy?n Th? HÃ¹ng",
    "Ph?m Vi?t Trang",
    "Tr?n Khanh",
    "LÃª Th? H?ng Minh",
    "Tr?n Th? ÄÃ ",
    "Phan Th? XuÃ¢n",
    "Tr?n Th? Hoa",
    "Cao Th? Thu? Trang",
]


def _build_household_docx() -> bytes:
    document = Document()
    document.add_paragraph(
        "DANH SÃCH H? GIA ÄÌ€NH ?NG H? KH?C PH?C THI?T H?I DO CON BÄ‚O S? 10 "
        "GÃ‚Y RA. THU KÃŠU G?I MTTP NGÃ€Y 6/10/2025"
    )
    document.add_paragraph("T? 40/HTT - PHU?NG HÌ‰A CU?NG")
    table = document.add_table(rows=1, cols=5)
    headers = ["TT", "H? vÃ  tÃªn", "Ä?a ch?", "S? ti?n(d)", "Ghi chÃº"]
    for cell, header in zip(table.rows[0].cells, headers, strict=True):
        cell.text = header

    amounts = ["600.000d"] * 34
    for index, (name, amount) in enumerate(zip(HOUSEHOLD_NAMES, amounts, strict=True), start=1):
        row = table.add_row().cells
        row[0].text = str(index)
        row[1].text = name
        row[2].text = f"Ä?a ch? {index}"
        row[3].text = amount
        row[4].text = ""

    total_row = table.add_row().cells
    total_row[0].text = ""
    total_row[1].text = "T?ng c?ng"
    total_row[2].text = ""
    total_row[3].text = "20.400.000d"
    total_row[4].text = ""

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _chunk_namespace(chunk: dict, *, document_id: UUID = DOCUMENT_ID):
    return SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=chunk["chunk_index"],
        content=chunk["content"],
        chunk_metadata=chunk["metadata"],
    )


def _fixture_chunks():
    parsed = DocxParser().parse(_build_household_docx()).text
    raw_chunks, _ = table_aware_chunk_text(parsed, chunk_size=1200)
    chunks = [_chunk_namespace(chunk) for chunk in raw_chunks]
    return parsed, raw_chunks, chunks


def test_docx_parser_serializes_household_table_rows() -> None:
    parsed = DocxParser().parse(_build_household_docx()).text

    assert "TABLE_TITLE table_id=docx_t1" in parsed
    assert parsed.count("TABLE_ROW table_id=docx_t1") >= 35
    assert all(name in parsed for name in HOUSEHOLD_NAMES)
    assert "20.400.000d" in parsed


def test_table_aware_chunking_emits_required_table_metadata() -> None:
    _parsed, raw_chunks, _chunks = _fixture_chunks()
    row_chunks = [
        chunk for chunk in raw_chunks if chunk["metadata"].get("chunk_type") == "table_row"
    ]
    block_chunks = [
        chunk for chunk in raw_chunks if chunk["metadata"].get("chunk_type") == "table_block"
    ]

    assert len(row_chunks) >= 35
    assert block_chunks
    first_row = row_chunks[0]
    metadata = first_row["metadata"]
    assert metadata["chunk_type"] == "table_row"
    assert metadata["chunk_mode"] == "table_aware"
    assert metadata["table_id"] == "docx_t1"
    assert metadata["table_title"]
    assert metadata["headers"] == ["TT", "H? vÃ  tÃªn", "Ä?a ch?", "S? ti?n(d)", "Ghi chÃº"]
    assert metadata["row_index"] == 1
    assert metadata["row_start"] == 1
    assert metadata["row_end"] == 1


def test_table_expansion_fetches_all_rows_when_retrieval_only_hits_header() -> None:
    _parsed, _raw_chunks, chunks = _fixture_chunks()
    primary = next(
        chunk for chunk in chunks if chunk.chunk_metadata["chunk_type"] == "table_header"
    )
    table_neighbors = [chunk for chunk in chunks if chunk.id != primary.id]

    class FakeRepoWithTable:
        def __init__(self) -> None:
            self.requested_document_id = None

        async def get_table_chunks(self, *, document_id, table_id, exclude_ids):
            self.requested_document_id = document_id
            assert table_id == "docx_t1"
            return [
                chunk
                for chunk in table_neighbors
                if chunk.document_id == document_id and chunk.id not in set(exclude_ids)
            ]

    repo = FakeRepoWithTable()
    service = RagAnswerService(
        chat_repository=repo,  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query=TABLE_QUERY,
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=3000,
        )
    )

    contents = "\n".join(chunk.chunk.content for chunk in expanded)
    assert repo.requested_document_id == DOCUMENT_ID
    assert all(name in contents for name in HOUSEHOLD_NAMES)
    assert "20.400.000d" in contents
    assert sum("TABLE_ROW table_id=docx_t1" in chunk.chunk.content for chunk in expanded) >= 35


def test_table_list_query_prompt_contains_all_rows_total_and_no_other_document() -> None:
    _parsed, _raw_chunks, chunks = _fixture_chunks()
    primary = next(chunk for chunk in chunks if chunk.chunk_metadata["chunk_type"] == "table_title")
    other_doc_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=OTHER_DOCUMENT_ID,
        chunk_index=1,
        content="TABLE_ROW table_id=docx_t1 row=1 | H? vÃ  tÃªn: PoC ThinkLabs",
        chunk_metadata={"chunk_type": "table_row", "table_id": "docx_t1"},
    )

    class FakeRepoWithScopedTable:
        async def get_table_chunks(self, *, document_id, table_id, exclude_ids):
            assert document_id == DOCUMENT_ID
            assert document_id != OTHER_DOCUMENT_ID
            return [chunk for chunk in chunks if chunk.id != primary.id] + [other_doc_chunk]

    service = RagAnswerService(
        chat_repository=FakeRepoWithScopedTable(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )
    expanded = asyncio.run(
        service._expand_with_neighbors(
            query=TABLE_QUERY,
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=3000,
        )
    )
    same_document_expanded = [
        chunk for chunk in expanded if chunk.chunk.document_id == DOCUMENT_ID
    ]
    prompt = RagAnswerService._build_user_prompt(
        query=TABLE_QUERY,
        context_chunks=same_document_expanded,
    )

    assert "ENTITY_MATCHED_ROWS:" in prompt
    assert all(name in prompt for name in HOUSEHOLD_NAMES)
    assert "20.400.000d" in prompt
    assert "PoC ThinkLabs" not in prompt
    assert prompt.count("ÄoÃ n T?n ChÃ¢u") == 1


def test_table_row_deduplication_keeps_distinct_rows() -> None:
    _parsed, _raw_chunks, chunks = _fixture_chunks()
    row_chunks = [chunk for chunk in chunks if chunk.chunk_metadata["chunk_type"] == "table_row"]
    duplicated = [
        ContextChunk(citation_index=1, chunk=row_chunks[0]),
        ContextChunk(citation_index=2, chunk=row_chunks[0]),
        ContextChunk(citation_index=3, chunk=row_chunks[1]),
        ContextChunk(citation_index=4, chunk=row_chunks[2]),
    ]
    service = RagAnswerService(
        chat_repository=SimpleNamespace(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    deduplicated = service._deduplicate_context_chunks(duplicated)

    assert len(deduplicated) == 3
    assert [item.citation_index for item in deduplicated] == [1, 2, 3]
    assert "ÄoÃ n T?n ChÃ¢u" in deduplicated[0].chunk.content
    assert "Khuong Thanh HÃ " in deduplicated[1].chunk.content
    assert "Tr?n ÄÌnh Thanh" in deduplicated[2].chunk.content
