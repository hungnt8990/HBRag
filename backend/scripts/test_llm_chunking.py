from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.services.llms.factory import build_llm_provider_or_error
from app.services.llm_chunking_service import LLMChunkingService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = PROJECT_ROOT / "log" / "llm_chunking_test_result.md"


async def load_document_by_id(document_id: UUID) -> Document | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()


async def load_document_by_id_vb(id_vb: str) -> Document | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.document_metadata["id_vb"].astext == id_vb)
        )
        return result.scalar_one_or_none()


def pick_text_for_experiment(document: Document) -> str:
    metadata = dict(document.document_metadata or {})

    for key in ("markdown_text", "plain_text", "noi_dung_clean", "noi_dung_raw"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return str(document.parsed_text or "").strip()


def metadata_for_prompt(document: Document) -> dict:
    metadata = dict(document.document_metadata or {})
    return {
        **metadata,
        "title": document.title,
        "document_code": metadata.get("document_code") or metadata.get("ky_hieu"),
        "issued_date": metadata.get("issued_date") or metadata.get("ngay_vb"),
        "issuer": metadata.get("issuer") or metadata.get("noi_ban_hanh"),
        "signer": metadata.get("signer") or metadata.get("nguoi_ky"),
    }


def write_markdown_log(
    *,
    document: Document,
    source_text: str,
    chunks,
) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("\n---\n")
    lines.append(f"# LLM chunking - {datetime.now().isoformat(timespec='seconds')}\n")
    lines.append(f"- Document ID: `{document.id}`")
    lines.append(f"- Title: `{document.title}`")
    lines.append(f"- Source text chars: `{len(source_text)}`")
    lines.append(f"- LLM chunk count: `{len(chunks)}`")
    lines.append("")

    for index, chunk in enumerate(chunks, start=1):
        lines.append(f"## Chunk {index}: {chunk.title}")
        lines.append(f"- Type: `{chunk.chunk_type}`")
        lines.append(f"- Heading path: `{' > '.join(chunk.heading_path)}`")
        lines.append(f"- Content chars: `{len(chunk.content)}`")
        lines.append(f"- Reason: {chunk.reason}")
        lines.append("")
        lines.append("```text")
        lines.append(chunk.content)
        lines.append("```")
        lines.append("")

    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", type=str, default=None)
    parser.add_argument("--id-vb", type=str, default=None)
    parser.add_argument("--max-input-chars", type=int, default=7000)
    parser.add_argument("--max-chunk-chars", type=int, default=2600)
    args = parser.parse_args()

    if not args.document_id and not args.id_vb:
        raise SystemExit("Bạn cần truyền --document-id hoặc --id-vb")

    if args.document_id:
        document = await load_document_by_id(UUID(args.document_id))
    else:
        document = await load_document_by_id_vb(args.id_vb)

    if document is None:
        raise SystemExit("Không tìm thấy document.")

    source_text = pick_text_for_experiment(document)
    if not source_text:
        raise SystemExit("Document không có text để thử LLM chunking.")

    llm_provider = build_llm_provider_or_error()
    service = LLMChunkingService(
        llm_provider,
        max_input_chars=args.max_input_chars,
        max_chunk_chars=args.max_chunk_chars,
    )

    chunks = await service.chunk_text(
        text=source_text,
        document_metadata=metadata_for_prompt(document),
    )

    write_markdown_log(
        document=document,
        source_text=source_text,
        chunks=chunks,
    )

    print(f"Done. LLM chunks: {len(chunks)}")
    print(f"Log file: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())