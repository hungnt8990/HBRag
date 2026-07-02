"""Sparse embedding HỌC ĐƯỢC (BGE-M3/SPLADE) qua HTTP — module ĐỘC LẬP.

Thiết kế cố ý tách rời khỏi Qdrant/ingestion: chỉ implement protocol
``SparseEmbeddingProvider`` (embed_texts/embed_query -> ``SparseEmbedding``),
KHÔNG import vector_store hay pipeline nào -> khi Qdrant đổi cấu trúc lưu
metadata/chunk (recreate collection, đổi payload...) module này dùng lại nguyên vẹn.

Endpoint cấu hình qua settings (``sparse_learned_*``); hỗ trợ 2 dạng response phổ biến:

1. ``{"data": [{"indices": [...], "values": [...]}, ...]}`` — server trả index vocab sẵn
   (TEI/Infinity/gateway đã map token -> id). Dùng trực tiếp.
2. ``{"data": [{"token_weights": {"điều": 0.83, ...}}, ...]}`` (hoặc key ``sparse``/
   ``lexical_weights`` kiểu BGE-M3) — map token -> index bằng blake2b hash CÙNG KHÔNG GIAN
   với ``HashingSparseEmbeddingProvider`` (``sparse_embedding_hash_dimensions``) để không
   phụ thuộc vocab của server (đổi server sparse vẫn cùng không gian index).

Lỗi endpoint: nếu ``sparse_learned_fallback_hashing=True`` -> fallback hashing (job
``run_qdrant.bat`` không gãy giữa chừng, có log cảnh báo); False -> raise để caller quyết.

⚠️ Dữ liệu index bằng provider/không gian nào thì QUERY phải cùng provider đó. Đổi
provider -> re-embed bằng ``scripts/reset_doffice_for_rechunk.py`` + ``run_qdrant.bat``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from app.services.embeddings.embedding_sparse import (
    HashingSparseEmbeddingProvider,
    SparseEmbedding,
)

logger = logging.getLogger(__name__)

# Các key thường gặp chứa map {token: weight} trong response sparse của các server khác nhau.
_TOKEN_WEIGHT_KEYS = ("token_weights", "lexical_weights", "sparse", "weights")


class LearnedSparseEmbeddingProvider:
    """Gọi HTTP endpoint sparse (BGE-M3/SPLADE) và chuẩn hoá về ``SparseEmbedding``."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        endpoint_path: str = "/embeddings/sparse",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        batch_size: int = 16,
        hash_dimensions: int = 1_048_576,
        hash_token_weights: bool = True,
        fallback: HashingSparseEmbeddingProvider | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("sparse_learned_base_url is required for the learned provider.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.endpoint_path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, int(batch_size))
        self.hash_dimensions = hash_dimensions
        self.hash_token_weights = hash_token_weights
        self._fallback = fallback

    # ------------------------------------------------- SparseEmbeddingProvider --
    async def embed_texts(self, texts: list[str]) -> list[SparseEmbedding]:
        if not texts:
            return []
        try:
            results: list[SparseEmbedding] = []
            for start in range(0, len(texts), self.batch_size):
                results.extend(await self._request(texts[start : start + self.batch_size]))
            return results
        except Exception:
            if self._fallback is None:
                raise
            logger.warning(
                "Learned sparse endpoint lỗi -> fallback hashing (%d texts).", len(texts), exc_info=True
            )
            return await self._fallback.embed_texts(texts)

    async def embed_query(self, query: str) -> SparseEmbedding:
        results = await self.embed_texts([query])
        return results[0] if results else SparseEmbedding(indices=[], values=[])

    # ----------------------------------------------------------------- HTTP --
    async def _request(self, texts: list[str]) -> list[SparseEmbedding]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "input": texts}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.base_url}{self.endpoint_path}", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Sparse endpoint lỗi HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return self._parse_response(resp.json(), expected=len(texts))

    def _parse_response(self, body: Any, *, expected: int) -> list[SparseEmbedding]:
        items = body.get("data") if isinstance(body, dict) else body
        if not isinstance(items, list):
            raise ValueError("Sparse endpoint trả response không có list 'data'.")
        if len(items) != expected:
            raise ValueError(
                f"Sparse endpoint trả {len(items)} kết quả, mong đợi {expected}."
            )
        return [self._parse_item(item) for item in items]

    def _parse_item(self, item: Any) -> SparseEmbedding:
        if not isinstance(item, dict):
            raise ValueError("Sparse endpoint trả item không phải object.")
        # Dạng 1: indices + values sẵn (server đã map vocab).
        indices = item.get("indices")
        values = item.get("values")
        if isinstance(indices, list) and isinstance(values, list) and len(indices) == len(values):
            return SparseEmbedding(
                indices=[int(i) for i in indices],
                values=[float(v) for v in values],
            )
        # Dạng 2: {token: weight} (BGE-M3 lexical_weights...) -> hash token về cùng không gian.
        if self.hash_token_weights:
            for key in _TOKEN_WEIGHT_KEYS:
                weights = item.get(key)
                if isinstance(weights, dict) and weights:
                    return self._from_token_weights(weights)
        raise ValueError("Sparse endpoint trả item không có 'indices/values' hoặc token weights.")

    def _from_token_weights(self, weights: dict[str, Any]) -> SparseEmbedding:
        merged: dict[int, float] = {}
        for token, weight in weights.items():
            try:
                value = float(weight)
            except (TypeError, ValueError):
                continue
            if value <= 0.0 or not str(token).strip():
                continue
            index = self._hash_token(str(token).casefold())
            # Token khác nhau hash trùng index (hiếm) -> giữ trọng số lớn nhất.
            merged[index] = max(merged.get(index, 0.0), value)
        indices = sorted(merged)
        return SparseEmbedding(indices=indices, values=[merged[i] for i in indices])

    def _hash_token(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False) % self.hash_dimensions
