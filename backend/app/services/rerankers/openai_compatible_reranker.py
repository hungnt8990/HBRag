from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.services.rerankers.base import RerankCandidate, RerankScore


class OpenAICompatibleReranker:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        endpoint_path: str = "/rerank",
    ) -> None:
        self._url = self._build_url(base_url=base_url, endpoint_path=endpoint_path)
        self._api_key = api_key
        self._model = model

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        if not candidates:
            return []

        response = await self._post_rerank(
            {
                "model": self._model,
                "query": query,
                "documents": [candidate.content for candidate in candidates],
            }
        )
        scores = self._extract_scores(response=response, expected_count=len(candidates))
        return [
            RerankScore(chunk_id=candidate.chunk_id, score=score)
            for candidate, score in zip(candidates, scores, strict=True)
        ]

    async def _post_rerank(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self._url,
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
    def _build_url(*, base_url: str, endpoint_path: str) -> str:
        path = endpoint_path.strip()
        if path.startswith(("http://", "https://")):
            return path.rstrip("/")
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @classmethod
    def _extract_scores(
        cls,
        *,
        response: dict[str, Any],
        expected_count: int,
    ) -> list[float]:
        for key in ("scores", "results", "data"):
            raw_items = response.get(key)
            if raw_items is None:
                continue
            scores = cls._parse_score_items(raw_items=raw_items, expected_count=expected_count)
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
