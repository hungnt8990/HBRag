"""Embed các chunk DOffice và LƯU vào kho vector local ``tests/Chunk/data/`` (giống Qdrant).

Luồng (tái dùng ĐÚNG pipeline thật ``embed_to_qdrant`` -> ``VectorIndexingService``):

    id_vb -> _source (cache raw/ hoặc ES) -> normalize -> build_doffice_chunks
      -> [mỗi ChunkCreate] rag_chunk_from_database -> should_index_chunk
      -> build_embedding_text -> embed dense (gateway) + sparse (hashing)
      -> qdrant_payload (+ trường lọc cấp VB) -> upsert Collection ``chunk_test_chunks``
    + 1 point docmeta/VB (embed trich_yeu+tom_tat+noi_ban_hanh) -> ``chunk_test_docmeta``

MỖI LẦN CHẠY XOÁ SẠCH ``data/`` rồi lưu lại (yêu cầu: build lại từ đầu mỗi lần test chunk).

Nguồn id_vb: tham số dòng lệnh (giống chunk_test). Embedding dùng gateway cấu hình trong
.env (EMBEDDING_BASE_URL/MODEL) -> cần mạng tới gateway. Batch embed đổi qua env
``CHUNK_TEST_EMBED_BATCH`` (mặc định 16; vector KHÔNG đổi theo batch, chỉ nhanh/chậm).

Cách dùng:
    python tests/Chunk/useChunk/build_vector_store.py 1382311 1068586
    (thường được run_chunk_test.bat gọi tự động sau khi ghi output text.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Cho phép import ``chunk_test`` (thư mục cha) + package ``app`` (qua common).
_CHUNK_DIR = Path(__file__).resolve().parents[1]
if str(_CHUNK_DIR) not in sys.path:
    sys.path.insert(0, str(_CHUNK_DIR))

from useChunk.chunk_vector_common import (  # noqa: E402
    CHUNKS_COLLECTION,
    DATA_DIR,
    DOCMETA_COLLECTION,
    MANIFEST_PATH,
    doc_uuid,
    fake_chunk,
    fake_document,
    make_store,
    open_client,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

import chunk_test  # noqa: E402  (load_cached_source / fetch_source / chunk_one / cfg)

from app.core.config import settings  # noqa: E402
from app.services.embeddings.embedding_factory import get_embedding_provider  # noqa: E402
from app.services.embeddings.embedding_sparse_factory import (  # noqa: E402
    get_sparse_embedding_provider,
)
from app.services.ingestion.ingestion_doffice_business_fields import (  # noqa: E402
    derive_business_fields,
)
from app.services.ingestion.ingestion_doffice_unified import (  # noqa: E402
    _DOCMETA_EMBED_FIELDS,
    _DOCMETA_FIELDS,
    _DOCMETA_NAMESPACE,
    _build_c1_doc_filter_payload,
)
from app.services.ingestion.ingestion_doffice_forward_schema import (  # noqa: E402
    build_forward_document_fields,
)
from app.services.rag.rag_chunk import (  # noqa: E402
    build_embedding_text,
    qdrant_payload,
    rag_chunk_from_database,
    should_index_chunk,
    stable_point_id,
)

import uuid as _uuid  # noqa: E402

EMBED_BATCH = int(os.getenv("CHUNK_TEST_EMBED_BATCH", "16") or "16")


async def _reset_collections(chunks_store, docmeta_store) -> None:
    """Xoá + tạo lại 2 collection TEST trên server (build lại từ đầu mỗi lần chạy).

    Chỉ đụng collection test (``chunk_test_*``) — KHÔNG bao giờ đụng prod ``hbrag_doffice_*``."""
    await chunks_store.recreate_collection()
    await docmeta_store.recreate_collection()


def _parse_ids(raw_args: list[str]) -> list[str]:
    ids: list[str] = []
    for raw in raw_args:
        ids.extend(
            part.strip()
            for part in raw.replace(";", ",").replace(" ", ",").split(",")
            if part.strip()
        )
    # Khử trùng lặp, giữ thứ tự.
    seen: set[str] = set()
    unique: list[str] = []
    for value in ids:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _load_source(client, id_vb: str, refresh: bool) -> dict | None:
    source = None if refresh else chunk_test.load_cached_source(id_vb)
    if source is None:
        source = chunk_test.fetch_source(client, id_vb)
        if source is not None:
            chunk_test.save_cached_source(id_vb, source)
    return source


async def _embed_chunks_for_doc(
    *,
    id_vb: str,
    source: dict,
    cfg: dict,
    chunks_store,
    dense_provider,
    sparse_provider,
) -> int:
    """Embed + upsert mọi chunk indexable của 1 văn bản. Trả số chunk đã ghi."""
    document_id = doc_uuid(id_vb)
    document = fake_document(id_vb, source, document_id)
    chunk_creates = chunk_test.chunk_one(source, cfg)  # list[ChunkCreate] (đã normalize+chunk)

    rag_chunks = [
        rag_chunk_from_database(
            fake_chunk(document_id, cc, id_vb),
            document=document,
            source_file=str(source.get("ten_file") or "document"),
            source_uri=None,
            use_enriched_content_for_embedding=False,
        )
        for cc in chunk_creates
    ]
    indexable = [rc for rc in rag_chunks if should_index_chunk(rc)]
    if not indexable:
        return 0

    embedding_texts = [build_embedding_text(rc) for rc in indexable]

    # Embed dense theo lô (vector không đổi theo kích thước lô). Sparse = hashing (local).
    dense_vectors: list = []
    for start in range(0, len(embedding_texts), max(1, EMBED_BATCH)):
        batch = embedding_texts[start : start + max(1, EMBED_BATCH)]
        dense_vectors.extend(await dense_provider.embed_texts(batch))
    sparse_vectors = await sparse_provider.embed_texts(embedding_texts) if sparse_provider else None

    # Trường lọc cấp văn bản gắn lên MỌI chunk (nam/thang/loai_vb/linh_vuc + tên chuẩn BA).
    src_business = {**source, **derive_business_fields(source)}
    doc_filter = {"id": str(document_id), **_build_c1_doc_filter_payload(src_business)}

    points = []
    for index, (rc, dense) in enumerate(zip(indexable, dense_vectors, strict=True)):
        payload = qdrant_payload(rc, store_raw_text=False)
        payload.update(doc_filter)
        sparse = sparse_vectors[index] if sparse_vectors is not None else None
        points.append(
            chunks_store.build_point(
                point_id=rc.database_chunk_id or stable_point_id(rc),
                vector=dense,
                sparse_vector=sparse,
                payload=payload,
            )
        )
    await chunks_store.upsert_chunks(points)
    return len(points)


async def _embed_docmeta(
    *, id_vb: str, source: dict, docmeta_store, dense_provider, sparse_provider
) -> None:
    """Col2: 1 point/văn bản — vector metadata (trich_yeu+tom_tat+noi_ban_hanh)."""
    document_id = doc_uuid(id_vb)
    src = {**source, **derive_business_fields(source)}
    embed_text = " ".join(
        str(src.get(f) or "").strip() for f in _DOCMETA_EMBED_FIELDS if src.get(f)
    ).strip() or str(src.get("id_vb") or "")
    dense = await dense_provider.embed_query(embed_text)
    sparse = await sparse_provider.embed_query(embed_text) if sparse_provider else None

    payload: dict = {"document_id": str(document_id), "id": str(document_id)}
    for field in _DOCMETA_FIELDS:
        value = src.get(field)
        if value not in (None, ""):
            payload[field] = value
    payload.update(build_forward_document_fields(src))

    point_id = str(_uuid.uuid5(_DOCMETA_NAMESPACE, f"docmeta:{src.get('id_vb')}"))
    point = docmeta_store.build_point(
        point_id=point_id, vector=dense, sparse_vector=sparse, payload=payload
    )
    await docmeta_store.upsert_chunks([point])


async def _run(id_list: list[str], refresh: bool) -> int:
    import httpx

    cfg = chunk_test.get_profile_config("doffice_admin")
    print(
        f"Embedding model : {settings.embedding_model} (dim={settings.embedding_dimension}) "
        f"@ {settings.embedding_base_url}"
    )
    print(f"Qdrant server   : {settings.qdrant_url}")
    print(
        f"Collections TEST: {CHUNKS_COLLECTION} (chunk) + {DOCMETA_COLLECTION} (docmeta) | "
        f"sparse={'on' if settings.sparse_embedding_enabled else 'off'} | embed_batch={EMBED_BATCH}\n"
    )

    dense_provider = get_embedding_provider()
    sparse_provider = (
        get_sparse_embedding_provider() if settings.sparse_embedding_enabled else None
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)  # nơi ghi manifest.json
    client = open_client()
    chunks_store = make_store(client, CHUNKS_COLLECTION)
    docmeta_store = make_store(client, DOCMETA_COLLECTION)
    await _reset_collections(chunks_store, docmeta_store)  # xoá + tạo lại collection test

    total_chunks = 0
    ok_docs = 0
    fail_docs = 0
    started = time.perf_counter()

    auth = (
        httpx.BasicAuth(chunk_test.ES_USER, chunk_test.ES_PASSWORD or "")
        if chunk_test.ES_USER
        else None
    )
    with httpx.Client(verify=chunk_test.ES_VERIFY_SSL, auth=auth, timeout=60.0) as es_client:
        for id_vb in id_list:
            try:
                source = _load_source(es_client, id_vb, refresh)
            except Exception as exc:  # noqa: BLE001
                print(f"[LỖI] id_vb={id_vb}: tải _source thất bại: {exc}")
                fail_docs += 1
                continue
            if source is None:
                print(f"[BỎ QUA] id_vb={id_vb}: không tìm thấy văn bản.")
                fail_docs += 1
                continue
            try:
                n = await _embed_chunks_for_doc(
                    id_vb=id_vb,
                    source=source,
                    cfg=cfg,
                    chunks_store=chunks_store,
                    dense_provider=dense_provider,
                    sparse_provider=sparse_provider,
                )
                await _embed_docmeta(
                    id_vb=id_vb,
                    source=source,
                    docmeta_store=docmeta_store,
                    dense_provider=dense_provider,
                    sparse_provider=sparse_provider,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[LỖI] id_vb={id_vb}: embed thất bại: {exc}")
                fail_docs += 1
                continue
            total_chunks += n
            ok_docs += 1
            print(f"[OK]  id_vb={id_vb}: {n:>4} chunk embed -> kho vector")

    await client.close()

    elapsed = time.perf_counter() - started
    manifest = {
        "qdrant_url": settings.qdrant_url,
        "collections": {"chunks": CHUNKS_COLLECTION, "docmeta": DOCMETA_COLLECTION},
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "sparse_enabled": settings.sparse_embedding_enabled,
        "documents_ok": ok_docs,
        "documents_failed": fail_docs,
        "total_chunks": total_chunks,
        "id_vb": id_list,
        "elapsed_seconds": round(elapsed, 2),
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"\nHoàn tất: {ok_docs} văn bản OK, {fail_docs} lỗi/bỏ qua, {total_chunks} chunk "
        f"đã embed trong {elapsed:.1f}s.\n"
        f"  -> Qdrant {settings.qdrant_url} | collection {CHUNKS_COLLECTION} + {DOCMETA_COLLECTION}"
    )
    return 0 if fail_docs == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Embed chunk DOffice vào kho vector local (giống Qdrant)."
    )
    parser.add_argument("id_vb", nargs="+", help="Một hoặc nhiều id_vb cần embed.")
    parser.add_argument(
        "--refresh", action="store_true", help="Ép tải lại _source từ ES (bỏ cache raw/)."
    )
    args = parser.parse_args()
    id_list = _parse_ids(args.id_vb)
    if not id_list:
        print("[LỖI] Không có id_vb nào.")
        return 1
    return asyncio.run(_run(id_list, args.refresh))


if __name__ == "__main__":
    try:
        import urllib3

        urllib3.disable_warnings()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
