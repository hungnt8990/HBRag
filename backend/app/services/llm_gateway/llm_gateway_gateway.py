from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from app.services.embeddings.embedding_base import EmbeddingProvider
from app.services.llms import LLMProvider
from app.services.llms.llm_factory import (
    build_llm_provider,
    build_llm_provider_or_error,
    get_llm_provider,
)
from app.services.rerankers.reranker_base import RerankCandidate, Reranker, RerankScore


@dataclass(frozen=True)
class LLMRequest:
    """A normalized request for every LLM call in the backend."""

    system_prompt: str
    user_prompt: str
    task_name: str | None = None


class LLMGateway:
    """
    Single entry point for LLM-related calls.

    Services should depend on this class instead of calling provider implementations
    directly. When the external LLM connection changes, update the gateway/client
    layer only.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._provider = provider
        self._embedding_provider = embedding_provider
        self._reranker = reranker

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        task_name: str | None = None,
    ) -> str:
        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_name=task_name,
        )
        return await self._provider.generate(
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
        )

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        task_name: str | None = None,
    ) -> str:
        return await self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_name=task_name,
        )

    async def stream_chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        task_name: str | None = None,
    ) -> AsyncIterator[str]:
        request = LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_name=task_name,
        )
        async for delta in self._provider.stream_generate(
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
        ):
            yield delta

    async def stream_generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        task_name: str | None = None,
    ) -> AsyncIterator[str]:
        async for delta in self.stream_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_name=task_name,
        ):
            yield delta

    async def embedding(self, texts: list[str]) -> list[list[float]]:
        return await self._get_embedding_provider().embed_texts(texts)

    async def embed_query(self, query: str) -> list[float]:
        return await self._get_embedding_provider().embed_query(query)

    async def enrich(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        task_name: str | None = None,
    ) -> str:
        return await self.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_name=task_name or "enrich",
        )

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        return await self._get_reranker().rerank(query=query, candidates=candidates)

    def _get_embedding_provider(self) -> EmbeddingProvider:
        if self._embedding_provider is None:
            from app.services.embeddings.embedding_factory import get_embedding_provider

            self._embedding_provider = get_embedding_provider()
        return self._embedding_provider

    def _get_reranker(self) -> Reranker:
        if self._reranker is None:
            from app.services.rerankers.reranker_factory import get_reranker

            self._reranker = get_reranker()
        return self._reranker


def build_llm_gateway(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMGateway:
    return LLMGateway(build_llm_provider(provider=provider, base_url=base_url, model=model))


def build_llm_gateway_or_error(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMGateway:
    return LLMGateway(build_llm_provider_or_error(provider=provider, base_url=base_url, model=model))


def get_llm_gateway() -> LLMGateway:
    return LLMGateway(get_llm_provider())
