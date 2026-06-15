from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹĐđ0-9]+(?:[._/-][A-Za-zÀ-ỹĐđ0-9]+)*", re.UNICODE)


@dataclass(frozen=True)
class SparseEmbedding:
    indices: list[int]
    values: list[float]


class SparseEmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[SparseEmbedding]: ...

    async def embed_query(self, query: str) -> SparseEmbedding: ...


class HashingSparseEmbeddingProvider:
    """Dependency-free lexical sparse encoder with stable hashed dimensions.

    It is not a learned SPLADE/BM25 model, but it preserves exact technical
    identifiers and provides a deterministic sparse channel for Qdrant hybrid
    retrieval. A learned provider can replace it behind the same protocol.
    """

    def __init__(self, *, dimensions: int = 1_048_576) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be greater than 0.")
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[SparseEmbedding]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, query: str) -> SparseEmbedding:
        return self._embed(query)

    def _embed(self, text: str) -> SparseEmbedding:
        counts: dict[int, int] = {}
        for token in self._tokens(text):
            index = self._hash_token(token)
            counts[index] = counts.get(index, 0) + 1
        if not counts:
            return SparseEmbedding(indices=[], values=[])

        indices = sorted(counts)
        values = [1.0 + math.log(float(counts[index])) for index in indices]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return SparseEmbedding(
            indices=indices,
            values=[value / norm for value in values],
        )

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens: list[str] = []
        for match in TOKEN_RE.finditer(text or ""):
            original = match.group(0)
            folded = original.casefold()
            tokens.append(folded)
            # Technical identifiers benefit from both whole-token and component matches.
            if any(separator in original for separator in ("_", ".", "/", "-")):
                tokens.extend(
                    part.casefold()
                    for part in re.split(r"[._/-]+", original)
                    if len(part) >= 2
                )
        return tokens

    def _hash_token(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False) % self.dimensions
