from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        response = await self._post_chat_completion(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        )
        return self._extract_content(response)

    async def stream_generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    delta = self._parse_sse_line(line)
                    if delta:
                        yield delta

    @staticmethod
    def _parse_sse_line(line: str) -> str:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return ""

        data = stripped[len("data:"):].strip()
        if not data or data == "[DONE]":
            return ""

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return ""

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""

        delta = first_choice.get("delta")
        if not isinstance(delta, dict):
            return ""

        content = delta.get("content")
        return content if isinstance(content, str) else ""

    async def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _extract_content(response: dict[str, Any]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Chat completion response must include choices.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError("Chat completion choice must be an object.")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Chat completion choice must include a message object.")

        content = message.get("content")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        return str(content)


OpenAILLM = OpenAICompatibleLLM
