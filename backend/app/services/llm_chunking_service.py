from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.services.llms import LLMProvider
from app.services.llm_chunking_prompts import (
    LLM_CHUNKING_SYSTEM_PROMPT,
    LLM_CHUNKING_USER_PROMPT_TEMPLATE,
)


@dataclass(slots=True)
class LLMSemanticChunk:
    title: str
    chunk_type: str
    heading_path: list[str]
    content: str
    reason: str


class LLMChunkingService:
    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        max_input_chars: int = 7000,
        max_chunk_chars: int = 2600,
    ) -> None:
        self._llm_provider = llm_provider
        self._max_input_chars = max_input_chars
        self._max_chunk_chars = max_chunk_chars

    async def chunk_text(
        self,
        *,
        text: str,
        document_metadata: dict[str, Any] | None = None,
    ) -> list[LLMSemanticChunk]:
        metadata = document_metadata or {}
        sections = split_text_for_llm_chunking(
            text,
            max_chars=self._max_input_chars,
        )

        output: list[LLMSemanticChunk] = []
        for section in sections:
            chunks = await self._chunk_one_section(
                section_text=section,
                document_metadata=metadata,
            )
            output.extend(chunks)

        return output

    async def _chunk_one_section(
        self,
        *,
        section_text: str,
        document_metadata: dict[str, Any],
    ) -> list[LLMSemanticChunk]:
        prompt = LLM_CHUNKING_USER_PROMPT_TEMPLATE.format(
            document_title=document_metadata.get("title") or "",
            document_code=document_metadata.get("document_code") or document_metadata.get("ky_hieu") or "",
            issued_date=document_metadata.get("issued_date") or "",
            issuer=document_metadata.get("issuer") or document_metadata.get("noi_ban_hanh") or "",
            signer=document_metadata.get("signer") or document_metadata.get("nguoi_ky") or "",
            section_text=section_text,
        )

        raw = await self._llm_provider.generate(
            system_prompt=LLM_CHUNKING_SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        parsed = parse_llm_chunk_response(raw)
        return validate_llm_chunks(
            parsed,
            fallback_text=section_text,
            max_chunk_chars=self._max_chunk_chars,
        )


def split_text_for_llm_chunking(text: str, *, max_chars: int) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []

    blocks = re.split(r"\n\s*\n", clean)
    sections: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        block_len = len(block)
        if current and current_len + block_len + 2 > max_chars:
            sections.append("\n\n".join(current).strip())
            current = [block]
            current_len = block_len
        else:
            current.append(block)
            current_len += block_len + 2

    if current:
        sections.append("\n\n".join(current).strip())

    return sections


def parse_llm_chunk_response(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def validate_llm_chunks(
    parsed: dict[str, Any],
    *,
    fallback_text: str,
    max_chunk_chars: int,
) -> list[LLMSemanticChunk]:
    raw_chunks = parsed.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        return [
            LLMSemanticChunk(
                title="Fallback chunk",
                chunk_type="llm_section_chunk",
                heading_path=[],
                content=fallback_text.strip(),
                reason="LLM không trả về chunks hợp lệ, dùng fallback.",
            )
        ]

    output: list[LLMSemanticChunk] = []

    for index, item in enumerate(raw_chunks, start=1):
        if not isinstance(item, dict):
            continue

        content = str(item.get("content") or "").strip()
        if not content:
            continue

        heading_path = item.get("heading_path")
        if not isinstance(heading_path, list):
            heading_path = []

        if len(content) > max_chunk_chars:
            split_parts = _split_long_chunk(content, max_chars=max_chunk_chars)
            for part_index, part in enumerate(split_parts, start=1):
                output.append(
                    LLMSemanticChunk(
                        title=f"{item.get('title') or f'LLM chunk {index}'} - phần {part_index}",
                        chunk_type=str(item.get("chunk_type") or "llm_section_chunk"),
                        heading_path=[str(value) for value in heading_path],
                        content=part,
                        reason=str(item.get("reason") or "Chunk dài được tách nhỏ sau validate."),
                    )
                )
            continue

        output.append(
            LLMSemanticChunk(
                title=str(item.get("title") or f"LLM chunk {index}"),
                chunk_type=str(item.get("chunk_type") or "llm_section_chunk"),
                heading_path=[str(value) for value in heading_path],
                content=content,
                reason=str(item.get("reason") or ""),
            )
        )

    if not output:
        output.append(
            LLMSemanticChunk(
                title="Fallback chunk",
                chunk_type="llm_section_chunk",
                heading_path=[],
                content=fallback_text.strip(),
                reason="Không có chunk hợp lệ sau validate, dùng fallback.",
            )
        )

    return output


def _split_long_chunk(text: str, *, max_chars: int) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text.strip())
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if current and current_len + len(paragraph) + 2 > max_chars:
            parts.append("\n\n".join(current).strip())
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += len(paragraph) + 2

    if current:
        parts.append("\n\n".join(current).strip())

    return parts or [text[:max_chars]]