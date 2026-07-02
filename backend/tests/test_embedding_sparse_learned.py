"""Tests cho sparse hoc duoc (BGE-M3/SPLADE) — embedding_sparse_learned.py."""

from __future__ import annotations

import asyncio

from app.services.embeddings import embedding_sparse_learned as mod
from app.services.embeddings.embedding_sparse import HashingSparseEmbeddingProvider


class _FakeResp:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    captured: dict = {}

    def __init__(self, resp) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        return self._resp


def _provider(**kwargs) -> mod.LearnedSparseEmbeddingProvider:
    defaults = dict(
        base_url="http://sparse.local/v1",
        model="BAAI/bge-m3",
        endpoint_path="/embeddings/sparse",
        hash_dimensions=1_048_576,
    )
    defaults.update(kwargs)
    return mod.LearnedSparseEmbeddingProvider(**defaults)


def test_parse_indices_values(monkeypatch) -> None:
    payload = {"data": [{"indices": [3, 7], "values": [0.5, 0.25]}]}
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload)))
    result = asyncio.run(_provider().embed_query("dieu 10 thoa uoc"))
    assert result.indices == [3, 7]
    assert result.values == [0.5, 0.25]
    assert _FakeClient.captured["json"]["model"] == "BAAI/bge-m3"
    assert _FakeClient.captured["url"] == "http://sparse.local/v1/embeddings/sparse"


def test_parse_token_weights_hashes_same_space_as_hashing_provider(monkeypatch) -> None:
    """Token -> index phai CUNG khong gian voi HashingSparseEmbeddingProvider."""
    payload = {"data": [{"lexical_weights": {"258/QD-IT": 0.9}}]}
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload)))
    provider = _provider()
    result = asyncio.run(provider.embed_query("258/QD-IT"))
    hashing = HashingSparseEmbeddingProvider(dimensions=1_048_576)
    expected_index = hashing._hash_token("258/qd-it")
    assert result.indices == [expected_index]
    assert result.values == [0.9]


def test_fallback_hashing_on_http_error(monkeypatch) -> None:
    payload = {"error": "boom"}
    monkeypatch.setattr(
        mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload, status_code=500))
    )
    fallback = HashingSparseEmbeddingProvider(dimensions=1_048_576)
    provider = _provider(fallback=fallback)
    result = asyncio.run(provider.embed_query("thoa uoc lao dong tap the"))
    # Fallback hashing van tra sparse hop le -> job khong gay.
    assert result.indices and result.values


def test_no_fallback_raises_on_http_error(monkeypatch) -> None:
    payload = {"error": "boom"}
    monkeypatch.setattr(
        mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload, status_code=500))
    )
    provider = _provider(fallback=None)
    try:
        asyncio.run(provider.embed_query("x"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when no fallback")


def test_factory_learned_branch(monkeypatch) -> None:
    from app.services.embeddings import embedding_sparse_factory as factory

    monkeypatch.setattr(factory.settings, "sparse_embedding_enabled", True)
    monkeypatch.setattr(factory.settings, "sparse_embedding_provider", "learned")
    monkeypatch.setattr(factory.settings, "sparse_learned_base_url", "http://sparse.local/v1")
    factory.get_sparse_embedding_provider.cache_clear()
    provider = factory.get_sparse_embedding_provider()
    assert isinstance(provider, mod.LearnedSparseEmbeddingProvider)
    assert provider._fallback is not None  # fallback hashing bat mac dinh
    factory.get_sparse_embedding_provider.cache_clear()
