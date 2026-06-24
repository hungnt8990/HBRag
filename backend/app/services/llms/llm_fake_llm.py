from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

CONTEXT_MARKER_PATTERN = re.compile(r"^\[(\d+)\]\s+", flags=re.MULTILINE)
QUESTION_PATTERN = re.compile(r"Question:\s*(.*?)\n\nContext:", flags=re.DOTALL)
STREAM_DELAY_SECONDS = 0.005


class FakeLLM:
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return self._build_answer(user_prompt)

    async def stream_generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncIterator[str]:
        answer = self._build_answer(user_prompt)
        tokens = answer.split(" ")
        for index, token in enumerate(tokens):
            delta = token if index == 0 else f" {token}"
            await asyncio.sleep(STREAM_DELAY_SECONDS)
            yield delta

    def _build_answer(self, user_prompt: str) -> str:
        question = self._extract_question(user_prompt)
        markers = CONTEXT_MARKER_PATTERN.findall(user_prompt)
        citation_suffix = " ".join(f"[{marker}]" for marker in markers[:3])
        if not citation_suffix:
            citation_suffix = "[no context]"

        return (
            "Generated from provided context. "
            f"Question: {question}. "
            f"Relevant citations: {citation_suffix}"
        )

    @staticmethod
    def _extract_question(user_prompt: str) -> str:
        match = QUESTION_PATTERN.search(user_prompt)
        if match is None:
            return "unknown"
        question = " ".join(match.group(1).split())
        return question or "unknown"
