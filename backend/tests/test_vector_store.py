import asyncio
import logging
from types import SimpleNamespace
from uuid import UUID

from qdrant_client.models import Distance, PointStruct

from app.services.vector_store import QdrantVectorStore


class FakeQdrantClient:
    def __init__(
        self,
        *,
        collection_exists: bool = True,
        vector_size: int = 384,
        distance: Distance = Distance.COSINE,
    ) -> None:
        self.upsert_batches: list[list[PointStruct]] = []
        self.exists = collection_exists
        self.vector_size = vector_size
        self.distance = distance
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []

    async def collection_exists(self, *, collection_name: str) -> bool:
        return self.exists

    async def create_collection(self, *, collection_name: str, vectors_config) -> None:
        self.exists = True
        self.vector_size = vectors_config.size
        self.distance = vectors_config.distance
        self.create_calls.append(
            {
                "collection_name": collection_name,
                "vector_size": vectors_config.size,
                "distance": vectors_config.distance,
            }
        )

    async def delete_collection(self, *, collection_name: str) -> None:
        self.exists = False
        self.delete_calls.append(collection_name)

    async def get_collection(self, *, collection_name: str):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=SimpleNamespace(size=self.vector_size, distance=self.distance)
                )
            )
        )

    async def upsert(self, *, collection_name: str, points: list[PointStruct]) -> None:
        self.upsert_batches.append(points)


def test_qdrant_vector_store_upserts_points_in_batches() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient()
        store = QdrantVectorStore(
            client=client,  # type: ignore[arg-type]
            collection_name="test_chunks",
            vector_size=2,
            upsert_batch_size=2,
        )
        points = [
            store.build_point(
                chunk_id=UUID(f"00000000-0000-0000-0000-00000000000{index}"),
                document_id=UUID("11111111-1111-1111-1111-111111111111"),
                chunk_index=index,
                content=f"Chunk {index}",
                metadata={},
                vector=[0.1, 0.2],
            )
            for index in range(5)
        ]

        await store.upsert_chunks(points)

        assert [len(batch) for batch in client.upsert_batches] == [2, 2, 1]

    asyncio.run(run_test())


def test_qdrant_vector_store_creates_missing_collection_with_config() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=False)
        store = QdrantVectorStore(
            client=client,  # type: ignore[arg-type]
            collection_name="test_chunks",
            vector_size=768,
        )

        info = await store.validate_collection_config()

        assert info.matches_config is True
        assert info.vector_size == 768
        assert info.distance == "Cosine"
        assert client.create_calls == [
            {
                "collection_name": "test_chunks",
                "vector_size": 768,
                "distance": Distance.COSINE,
            }
        ]

    asyncio.run(run_test())


def test_qdrant_vector_store_warns_on_dimension_mismatch_without_recreate(caplog) -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=True, vector_size=384)
        store = QdrantVectorStore(
            client=client,  # type: ignore[arg-type]
            collection_name="test_chunks",
            vector_size=768,
        )

        with caplog.at_level(logging.WARNING):
            info = await store.validate_collection_config(auto_recreate=False)

        assert info.matches_config is False
        assert info.vector_size == 384
        assert info.expected_vector_size == 768
        assert info.recreated is False
        assert client.delete_calls == []
        assert client.create_calls == []
        assert "Qdrant collection vector size mismatch" in caplog.text

    asyncio.run(run_test())


def test_qdrant_vector_store_auto_recreates_on_dimension_mismatch() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=True, vector_size=384)
        store = QdrantVectorStore(
            client=client,  # type: ignore[arg-type]
            collection_name="test_chunks",
            vector_size=768,
        )

        info = await store.validate_collection_config(auto_recreate=True)

        assert info.matches_config is True
        assert info.vector_size == 768
        assert info.recreated is True
        assert client.delete_calls == ["test_chunks"]
        assert client.create_calls == [
            {
                "collection_name": "test_chunks",
                "vector_size": 768,
                "distance": Distance.COSINE,
            }
        ]

    asyncio.run(run_test())
