from __future__ import annotations

from collections.abc import Sequence

from app.services.llm_gateway.llm_gateway_external_client import ExternalLLMClient
from app.services.rerankers.reranker_base import RerankCandidate, RerankScore


class OpenAICompatibleReranker:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        endpoint_path: str = "/rerank",
        client: ExternalLLMClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._endpoint_path = endpoint_path
        self._client = client or ExternalLLMClient(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
        )
        self._url = self._build_url(base_url=self._base_url, endpoint_path=endpoint_path)

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        if not candidates:
            return []

        scores = await self._client.rerank(
            query=query,
            documents=[candidate.content for candidate in candidates],
            model=self._model,
            endpoint_path=self._endpoint_path,
        )
        return [RerankScore(chunk_id=candidate.chunk_id, score=score) for candidate, score in zip(candidates, scores, strict=True)]

    async def _post_rerank(self, payload: dict[str, object]) -> dict[str, object]:
        return await self._client._post_json(
            endpoint_path=self._endpoint_path,
            payload=dict(payload),
        )

    def _headers(self) -> dict[str, str]:
        return self._client._headers()

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
        response: dict[str, object],
        expected_count: int,
    ) -> list[float]:
        return ExternalLLMClient._extract_rerank_scores(
            response=response,
            expected_count=expected_count,
        )

    @staticmethod
    def _parse_score_items(
        *,
        raw_items: object,
        expected_count: int,
    ) -> list[float] | None:
        return ExternalLLMClient._parse_score_items(
            raw_items=raw_items,
            expected_count=expected_count,
        )
