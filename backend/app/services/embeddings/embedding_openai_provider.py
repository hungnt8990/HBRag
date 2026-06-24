from __future__ import annotations

from app.services.llm_gateway.llm_gateway_external_client import ExternalLLMClient


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dimension: int,
        client: ExternalLLMClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self._client = client or ExternalLLMClient(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await self._client.embedding(texts, model=self.model)

    async def embed_query(self, query: str) -> list[float]:
        embeddings = await self.embed_texts([query])
        return embeddings[0]

    async def _post_embeddings(self, payload: dict[str, object]) -> dict[str, object]:
        return await self._client._post_json(endpoint_path="/embeddings", payload=dict(payload))

    def _headers(self) -> dict[str, str]:
        return self._client._headers()

    @staticmethod
    def _extract_embeddings(response: dict[str, object]) -> list[list[float]]:
        return ExternalLLMClient._extract_embeddings(response)


OpenAIEmbeddingProvider = OpenAICompatibleEmbeddingProvider
