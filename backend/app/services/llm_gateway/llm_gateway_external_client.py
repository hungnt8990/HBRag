from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class ExternalLLMClient:
    """Shared OpenAI-compatible client for external LLM services."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        response = await self._post_json(
            endpoint_path="/chat/completions",
            payload={
                "model": model or self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        return self._extract_chat_content(response)

    async def stream_chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                self._build_url("/chat/completions"),
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    delta = self._parse_sse_line(line)
                    if delta:
                        yield delta

    async def embedding(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []

        response = await self._post_json(
            endpoint_path="/embeddings",
            payload={
                "model": model or self.model,
                "input": texts,
            },
            timeout_seconds=30.0,
        )
        embeddings = self._extract_embeddings(response)
        if len(embeddings) != len(texts):
            raise RuntimeError("Embedding response size did not match input size.")
        return embeddings

    async def enrich(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        return await self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
        )

    async def rerank(
        self,
        *,
        query: str,
        documents: list[str],
        model: str | None = None,
        endpoint_path: str = "/rerank",
    ) -> list[float]:
        if not documents:
            return []

        response = await self._post_json(
            endpoint_path=endpoint_path,
            payload={
                "model": model or self.model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
        )
        return self._extract_rerank_scores(
            response=response,
            expected_count=len(documents),
        )

    async def _post_json(
        self,
        *,
        endpoint_path: str,
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds or self.timeout_seconds) as client:
            response = await client.post(
                self._build_url(endpoint_path),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_url(self, endpoint_path: str) -> str:
        path = endpoint_path.strip()
        if path.startswith(("http://", "https://")):
            return path.rstrip("/")
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _parse_sse_line(line: str) -> str:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return ""

        data = stripped[len("data:") :].strip()
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

    @staticmethod
    def _extract_chat_content(response: dict[str, Any]) -> str:
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
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content)

    @staticmethod
    def _extract_embeddings(response: dict[str, Any]) -> list[list[float]]:
        data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Embedding response must include a data list.")

        indexed_embeddings: list[tuple[int, list[float]]] = []
        for fallback_index, item in enumerate(data):
            if not isinstance(item, dict):
                raise RuntimeError("Embedding response data items must be objects.")

            raw_embedding = item.get("embedding")
            if not isinstance(raw_embedding, list):
                raise RuntimeError("Embedding response item is missing an embedding list.")

            raw_index = item.get("index", fallback_index)
            index = raw_index if isinstance(raw_index, int) else fallback_index
            indexed_embeddings.append((index, [float(value) for value in raw_embedding]))

        return [embedding for _, embedding in sorted(indexed_embeddings)]

    @classmethod
    def _extract_rerank_scores(
        cls,
        *,
        response: dict[str, Any],
        expected_count: int,
    ) -> list[float]:
        for key in ("scores", "results", "data"):
            raw_items = response.get(key)
            if raw_items is None:
                continue
            scores = cls._parse_score_items(
                raw_items=raw_items,
                expected_count=expected_count,
            )
            if scores is not None:
                return scores

        raise RuntimeError("Reranker response must include scores, results, or data.")

    @staticmethod
    def _parse_score_items(
        *,
        raw_items: Any,
        expected_count: int,
    ) -> list[float] | None:
        if not isinstance(raw_items, list):
            return None
        if len(raw_items) != expected_count:
            raise RuntimeError("Reranker response size did not match candidate size.")
        if all(isinstance(item, int | float) for item in raw_items):
            return [float(item) for item in raw_items]

        scores_by_index: dict[int, float] = {}
        for fallback_index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                return None

            score = item.get("relevance_score", item.get("score"))
            if not isinstance(score, int | float):
                return None

            raw_index = item.get("index", fallback_index)
            index = raw_index if isinstance(raw_index, int) else fallback_index
            scores_by_index[index] = float(score)

        if set(scores_by_index) != set(range(expected_count)):
            raise RuntimeError("Reranker response indexes did not match candidates.")
        return [scores_by_index[index] for index in range(expected_count)]
