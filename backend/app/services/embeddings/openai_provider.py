from __future__ import annotations

from typing import Any

import httpx


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dimension: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = await self._post_embeddings(
            {
                "model": self.model,
                "input": texts,
            }
        )
        embeddings = self._extract_embeddings(response)
        if len(embeddings) != len(texts):
            raise RuntimeError("Embedding response size did not match input size.")
        return embeddings

    async def embed_query(self, query: str) -> list[float]:
        embeddings = await self.embed_texts([query])
        return embeddings[0]

    async def _post_embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

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
            indexed_embeddings.append(
                (index, [float(value) for value in raw_embedding])
            )

        return [embedding for _, embedding in sorted(indexed_embeddings)]


OpenAIEmbeddingProvider = OpenAICompatibleEmbeddingProvider
