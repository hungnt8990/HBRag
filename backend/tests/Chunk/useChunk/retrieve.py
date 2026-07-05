"""Hỏi-đáp (RAG) trên kho vector test để KIỂM TRA chất lượng chunk: truy hồi -> LLM trả lời.

Tái dùng ĐÚNG thuật toán của backend (``app/services``):
  - ``build_query_embedding_text``  : bọc instruction cho query (chuẩn Qwen3-Embedding).
  - embed dense (gateway) + sparse (theo manifest) cho query.
  - ``QdrantVectorStore.search``    : hybrid dense+sparse hợp nhất RRF (Qdrant server).
  - ``get_reranker().rerank``       : rerank BAAI/bge-reranker-v2-m3 (tuỳ chọn).
  - **``build_system_prompt`` + ``LLMGateway.generate``** : LLM SINH CÂU TRẢ LỜI grounded trên
    các đoạn (passage) đánh số ``[i]`` như luồng ``RagAnswerService`` thật.

Không dùng ACL/CRAG/ES fusion (bộ test chunk gọn nhẹ): mục tiêu là xem CÙNG 1 câu hỏi thì
chunk sinh ra có truy hồi đúng + LLM có trả lời đúng dựa trên chunk hay không.

Cách dùng:
    python tests/Chunk/useChunk/retrieve.py "kế hoạch vốn sửa chữa lớn của CPCIT?"
    python tests/Chunk/useChunk/retrieve.py "3684/EVNCPC-KD nói về gì?" --top-k 8
    python tests/Chunk/useChunk/retrieve.py "..." --no-answer      # chỉ xem chunk truy hồi, không gọi LLM
    python tests/Chunk/useChunk/retrieve.py "..." --no-rerank --docmeta
Không truyền câu hỏi -> vào chế độ hỏi liên tục (gõ 'exit' để thoát).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_CHUNK_DIR = Path(__file__).resolve().parents[1]
if str(_CHUNK_DIR) not in sys.path:
    sys.path.insert(0, str(_CHUNK_DIR))

from useChunk.chunk_vector_common import (  # noqa: E402
    CHUNKS_COLLECTION,
    DOCMETA_COLLECTION,
    make_store,
    open_client,
    read_manifest,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

from app.core.config import settings  # noqa: E402
from app.services.embeddings.embedding_factory import get_embedding_provider  # noqa: E402
from app.services.embeddings.embedding_sparse_factory import (  # noqa: E402
    get_sparse_embedding_provider,
)
from app.services.rag.rag_chunk import build_query_embedding_text  # noqa: E402


def _fmt_result(rank: int, result, rerank_score: float | None) -> str:
    meta = result.metadata or {}
    section = meta.get("section_path") or meta.get("section_title") or ""
    if isinstance(section, (list, tuple)):
        section = " > ".join(str(p) for p in section)
    head = (
        f"#{rank:<2} vec={result.score:.4f}"
        + (f" rr={rerank_score:.4f}" if rerank_score is not None else "")
        + f" | id_vb={meta.get('id_vb') or ''} | {meta.get('ky_hieu') or meta.get('document_no') or ''}"
    )
    tags = f"chunk_type={meta.get('chunk_type')} chunk_order={meta.get('chunk_order')}"
    if section:
        tags += f" | {section}"
    body = " ".join(str(result.content or "").split())
    preview = body[:400] + ("…" if len(body) > 400 else "")
    return f"{head}\n     {tags}\n     {preview}"


def _fmt_source(rank: int, result, rerank_score: float | None) -> str:
    """Dòng nguồn gọn cho phần trả lời LLM (đoạn [rank] tương ứng)."""
    meta = result.metadata or {}
    section = meta.get("section_path") or meta.get("section_title") or ""
    if isinstance(section, (list, tuple)):
        section = " > ".join(str(p) for p in section)
    score = f"vec={result.score:.3f}" + (f" rr={rerank_score:.3f}" if rerank_score is not None else "")
    tail = f" | {section}" if section else ""
    return (
        f"  [{rank}] {meta.get('ky_hieu') or meta.get('document_no') or ''} "
        f"(id_vb={meta.get('id_vb') or ''}) · {meta.get('chunk_type')} · {score}{tail}"
    )


async def _llm_answer(query: str, context_results: list) -> str:
    """Sinh câu trả lời grounded bằng LLM của project trên các passage đánh số ``[i]``.

    Tái dùng ``build_system_prompt`` (prompt hệ thống RagAnswerService: hybrid + policy
    explainer + identifier-lookup) và ``LLMGateway.generate`` (LLM cấu hình trong .env)."""
    from app.services.llm_gateway.llm_gateway_gateway import get_llm_gateway
    from app.services.rag.rag_answer_service import build_system_prompt

    passages = "\n".join(
        f"[{i}] {' '.join(str(r.content or '').split())}"
        for i, r in enumerate(context_results, start=1)
    )
    system_prompt = build_system_prompt(answer_mode=None, answer_style=None, query=query)
    user_prompt = (
        "Document Text:\n"
        f"{passages}\n\n"
        "Trả lời câu hỏi CHỈ dựa trên các đoạn văn bản đánh số ở trên. Nếu tài liệu không có "
        "thông tin, hãy nói rõ một cách tự nhiên. Không tạo mục Nguồn/Tài liệu ở cuối.\n\n"
        f"Question:\n{query}"
    )
    return await get_llm_gateway().generate(
        system_prompt=system_prompt, user_prompt=user_prompt, task_name="chunk_test_answer"
    )


async def _rerank(query: str, results: list, top_k: int) -> list[tuple]:
    """Rerank kết quả bằng reranker backend. Trả list (result, rerank_score) đã sắp xếp.

    Nếu reranker lỗi (gateway không tới được) -> giữ nguyên thứ tự hybrid (rerank_score=None)."""
    from app.services.rerankers.reranker_base import RerankCandidate
    from app.services.rerankers.reranker_factory import get_reranker

    candidates = [
        RerankCandidate(chunk_id=str(i), content=str(r.content or ""))
        for i, r in enumerate(results)
    ]
    try:
        scores = await get_reranker().rerank(query=query, candidates=candidates)
    except Exception as exc:  # noqa: BLE001
        print(f"[cảnh báo] rerank bỏ qua (gateway lỗi: {exc}) -> dùng thứ tự hybrid.\n")
        return [(r, None) for r in results[:top_k]]
    by_id = {s.chunk_id: s.score for s in scores}
    # Ghép điểm reranker theo chỉ số ứng viên rồi sắp giảm dần (bỏ điểm None xuống cuối).
    paired = [(r, by_id.get(str(idx))) for idx, r in enumerate(results)]
    paired.sort(key=lambda p: (p[1] if p[1] is not None else -1e9), reverse=True)
    return paired[:top_k]


async def _search_once(
    *, query: str, store, dense_provider, sparse_provider,
    top_k: int, pool: int, rerank: bool, answer: bool,
) -> None:
    query_text = build_query_embedding_text(query)
    dense = await dense_provider.embed_query(query_text)
    sparse = await sparse_provider.embed_query(query_text) if sparse_provider else None
    results = await store.search(query_vector=dense, top_k=pool, sparse_query=sparse)

    if not results:
        print("(không có kết quả — kho vector rỗng? Hãy chạy build_vector_store trước.)\n")
        return

    if rerank:
        paired = await _rerank(query, results, top_k)
    else:
        paired = [(r, None) for r in results[:top_k]]

    if not answer:
        # Chế độ soi chunk: in đầy đủ đoạn truy hồi (không gọi LLM).
        print(f"Truy hồi: \"{query}\"  ->  {len(paired)} kết quả (pool hybrid={len(results)})\n")
        for rank, (result, rr) in enumerate(paired, start=1):
            print(_fmt_result(rank, result, rr))
            print()
        return

    # Chế độ RAG: LLM sinh câu trả lời dựa trên các đoạn đã truy hồi + rerank.
    context = [r for r, _ in paired]
    try:
        ans = await _llm_answer(query, context)
    except Exception as exc:  # noqa: BLE001
        print(f"[LỖI] LLM sinh câu trả lời thất bại: {exc}")
        print("      (dùng --no-answer để chỉ xem chunk truy hồi.)\n")
        return

    print(f"❓ {query}\n")
    print(f"💬 {ans.strip()}\n")
    print(f"📎 Nguồn ({len(paired)} đoạn, pool hybrid={len(results)}):")
    for rank, (result, rr) in enumerate(paired, start=1):
        print(_fmt_source(rank, result, rr))
    print()


async def _run(args) -> int:
    # Cấu hình LẤY THEO lần build gần nhất (manifest) -> khớp collection + cờ sparse đã tạo.
    manifest = read_manifest()
    cols = manifest.get("collections") or {}
    if args.docmeta:
        collection = cols.get("docmeta", DOCMETA_COLLECTION)
    else:
        collection = cols.get("chunks", CHUNKS_COLLECTION)
    sparse_enabled = bool(manifest.get("sparse_enabled", settings.sparse_embedding_enabled))
    dense_provider = get_embedding_provider()
    sparse_provider = get_sparse_embedding_provider() if sparse_enabled else None

    client = open_client()
    store = make_store(client, collection, sparse_enabled=sparse_enabled)
    try:
        if not await client.collection_exists(collection_name=collection):
            print(
                f"[LỖI] Collection '{collection}' chưa tồn tại trên {settings.qdrant_url}. "
                "Chạy build_vector_store.py (run_chunk_test.bat) trước."
            )
            return 1
        await store.ensure_collection()
        mode = "chunk-only (--no-answer)" if args.no_answer else f"RAG+LLM ({settings.llm_model})"
        print(
            f"Qdrant {settings.qdrant_url} | collection {collection} | mode={mode}\n"
            f"rerank={'off' if args.no_rerank else settings.reranker_model} "
            f"| top_k={args.top_k} pool={args.pool}\n"
        )
        queries = [" ".join(args.query).strip()] if args.query else []
        if queries and queries[0]:
            for q in queries:
                await _search_once(
                    query=q, store=store, dense_provider=dense_provider,
                    sparse_provider=sparse_provider, top_k=args.top_k, pool=args.pool,
                    rerank=not args.no_rerank, answer=not args.no_answer,
                )
        else:
            print("Chế độ hỏi liên tục. Gõ câu hỏi (Enter rỗng hoặc 'exit' để thoát).")
            while True:
                try:
                    q = input("\n❓ ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not q or q.lower() in {"exit", "quit", "q"}:
                    break
                await _search_once(
                    query=q, store=store, dense_provider=dense_provider,
                    sparse_provider=sparse_provider, top_k=args.top_k, pool=args.pool,
                    rerank=not args.no_rerank, answer=not args.no_answer,
                )
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Truy hồi thử trên kho vector local để test chất lượng chunk."
    )
    parser.add_argument("query", nargs="*", help="Câu truy vấn (bỏ trống -> hỏi liên tục).")
    parser.add_argument("--top-k", type=int, default=8, help="Số kết quả cuối (mặc định 8).")
    parser.add_argument(
        "--pool", type=int, default=30, help="Số ứng viên hybrid trước rerank (mặc định 30)."
    )
    parser.add_argument("--no-rerank", action="store_true", help="Bỏ bước rerank (chỉ hybrid).")
    parser.add_argument(
        "--no-answer", action="store_true",
        help="Không gọi LLM — chỉ in các chunk truy hồi (để soi chunk).",
    )
    parser.add_argument(
        "--docmeta", action="store_true", help="Tìm ở collection docmeta thay vì chunk."
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    try:
        import urllib3

        urllib3.disable_warnings()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
