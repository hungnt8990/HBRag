from typing import Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, query: str) -> list[float]: ...
