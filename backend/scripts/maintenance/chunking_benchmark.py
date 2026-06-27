"""Run retrieval and answer benchmarks for chunking strategies.

The benchmark is deterministic-first and works with the JSONL fixtures under
``docs/``. It reuses the existing artifact-first retrieval stack, then emits CSV
and Markdown summaries that can be regenerated after re-ingest / re-index.

Usage examples:
  python scripts/maintenance/chunking_benchmark.py
  python scripts/maintenance/chunking_benchmark.py --mode retrieval
  python scripts/maintenance/chunking_benchmark.py --mode both --limit 25
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.chat import ChatRepository
from app.repositories.document_logs import DocumentLogRepository
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository
from app.repositories.retrieval_logs import RetrievalLogRepository
from app.services.retrieval.retrieval_artifact_first_retrieval import ArtifactFirstRetrievalService
from app.services.retrieval.retrieval_elasticsearch_keyword_search import (
    ElasticsearchKeywordSearchService,
    get_elasticsearch_keyword_store,
)
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.retrieval.retrieval_hybrid_search import HybridSearchService
from app.services.retrieval.retrieval_keyword_search import KeywordSearchService
from app.services.knowledge.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.llm_gateway import get_llm_gateway
from app.services.queries.query_contract_service import QueryContractService
from app.services.rag.rag_answer_service import RagAnswerService
from app.services.rag.rag_runtime_config import default_rag_runtime_config, load_rag_runtime_config
from app.services.rerankers.reranker_service import RerankingService
from app.services.vector.vector_indexing_service import VectorIndexingService
from app.services.vector.vector_store import get_artifact_vector_store, get_vector_store

DEFAULT_BENCHMARK_DIR = Path("docs")
DEFAULT_QUESTIONS_FILE = "benchmark_questions.jsonl"
DEFAULT_EXPECTED_FILE = "expected_evidence.jsonl"
DEFAULT_RETRIEVAL_RESULTS_FILE = "retrieval_results.csv"
DEFAULT_ANSWER_RESULTS_FILE = "answer_results.csv"
DEFAULT_RETRIEVAL_REPORT_FILE = "retrieval_eval_report.md"
DEFAULT_ERROR_REPORT_FILE = "retrieval_error_analysis.md"

EVIDENCE_ALIASES: dict[str, set[str]] = {
    "document_summary_artifact": {"document_profile", "document_header"},
    "document_header": {"document_header", "document_profile"},
    "assignment_artifact": {"person_assignment_artifact", "table_row_artifact"},
    "entity_profile_artifact": {"document_profile", "person_assignment_artifact", "table_row_artifact"},
    "table_artifact": {"table_row_artifact", "table_summary", "table_title"},
    "table_row": {"table_row", "table_row_artifact"},
    "table_summary": {"table_summary", "table_title"},
    "legal_clause": {"policy_rule_artifact", "legal_evidence_artifact"},
    "legal_evidence_artifact": {"policy_rule_artifact", "legal_clause"},
    "deadline_artifact": {"procedure_artifact", "policy_rule_artifact"},
}


@dataclass(frozen=True)
class BenchmarkQuestion:
    question: str
    question_type: str
    expected_answer: str | None = None
    expected_doc_code: str | None = None
    expected_evidence: list[str] | None = None
    expected_row_index: str | None = None
    expected_article_number: str | None = None


@dataclass(frozen=True)
class BenchmarkRunResult:
    question: BenchmarkQuestion
    retrieved_labels: list[str]
    matched_labels: list[str]
    recall_at_5: bool
    recall_at_10: bool
    precision_at_5: float
    mrr: float
    wrong_document: bool | None
    wrong_row: bool | None
    wrong_article: bool | None
    answer: str | None = None
    citation_count: int | None = None
    answer_matches_expected: bool | None = None
    error: str | None = None


@dataclass
class BenchmarkServices:
    artifact_first_retrieval_service: ArtifactFirstRetrievalService
    rag_answer_service: RagAnswerService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run chunking benchmarks.")
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK_DIR))
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS_FILE)
    parser.add_argument("--expected-evidence", default=DEFAULT_EXPECTED_FILE)
    parser.add_argument("--output-dir", default=str(DEFAULT_BENCHMARK_DIR))
    parser.add_argument(
        "--mode",
        choices=("retrieval", "answer", "both"),
        default="both",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--use-graph", action="store_true")
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    questions_path = Path(args.questions)
    if not questions_path.is_absolute():
        questions_path = benchmark_dir / questions_path
    expected_path = Path(args.expected_evidence)
    if not expected_path.is_absolute():
        expected_path = benchmark_dir / expected_path

    questions = _load_questions(questions_path, limit=args.limit)
    expected_by_type = _load_expected_evidence(expected_path)
    questions = [_merge_expected_evidence(question, expected_by_type) for question in questions]

    async with AsyncSessionLocal() as session:
        services = await _build_services(session)
        results: list[BenchmarkRunResult] = []
        for question in questions:
            results.append(
                await _run_question(
                    question=question,
                    services=services,
                    mode=args.mode,
                    top_k=args.top_k,
                    candidate_k=args.candidate_k,
                    use_graph=args.use_graph,
                )
            )

    _write_csv(output_dir / DEFAULT_RETRIEVAL_RESULTS_FILE, [_retrieval_row(result) for result in results])
    _write_markdown_report(output_dir / DEFAULT_RETRIEVAL_REPORT_FILE, results=results, questions=len(results))
    _write_error_report(output_dir / DEFAULT_ERROR_REPORT_FILE, results=results)
    if args.mode in {"answer", "both"}:
        _write_csv(output_dir / DEFAULT_ANSWER_RESULTS_FILE, [_answer_row(result) for result in results])


async def _build_services(session) -> BenchmarkServices:
    document_repository = DocumentRepository(session)
    artifact_repository = KnowledgeArtifactRepository(session)
    retrieval_log_repository = RetrievalLogRepository(session)
    rag_runtime_config_repository = RagRuntimeConfigRepository(session)
    chat_repository = ChatRepository(session)
    document_log_repository = DocumentLogRepository(session)

    try:
        rag_config = await load_rag_runtime_config(rag_runtime_config_repository)
        await rag_runtime_config_repository.commit()
    except Exception:
        await rag_runtime_config_repository.rollback()
        rag_config = default_rag_runtime_config()

    llm_gateway = get_llm_gateway()
    sparse_embedding_provider = get_sparse_embedding_provider()
    vector_store = get_vector_store()
    artifact_vector_store = get_artifact_vector_store()

    vector_search_service = VectorIndexingService(
        repository=document_repository,
        llm_gateway=llm_gateway,
        vector_store=vector_store,
        sparse_embedding_provider=sparse_embedding_provider,
        keyword_index_store=get_elasticsearch_keyword_store() if settings.elasticsearch_enabled else None,
    )
    keyword_search_service = (
        ElasticsearchKeywordSearchService(
            store=get_elasticsearch_keyword_store(),
            fallback_service=KeywordSearchService(session) if settings.elasticsearch_fallback_to_postgres else None,
        )
        if settings.elasticsearch_enabled
        else KeywordSearchService(session)
    )
    hybrid_search_service = HybridSearchService(
        vector_search_service=vector_search_service,
        keyword_search_service=keyword_search_service,
        retrieval_log_repository=retrieval_log_repository,
    )
    reranking_service = RerankingService(
        hybrid_search_service=hybrid_search_service,
        llm_gateway=llm_gateway,
        retrieval_log_repository=retrieval_log_repository,
        chunk_repository=document_repository,
        graph_retrieval_service=None,
    )
    artifact_indexing_service = KnowledgeArtifactIndexingService(
        repository=artifact_repository,
        llm_gateway=llm_gateway,
        vector_store=artifact_vector_store,
        sparse_embedding_provider=sparse_embedding_provider,
    )
    artifact_first_retrieval_service = ArtifactFirstRetrievalService(
        artifact_repository=artifact_repository,
        artifact_indexing_service=artifact_indexing_service,
        reranking_service=reranking_service,
        query_contract_service=QueryContractService(),
        rag_config=rag_config,
    )
    rag_answer_service = RagAnswerService(
        chat_repository=chat_repository,
        reranking_service=reranking_service,
        llm_provider=get_llm_gateway(),
        document_log_repository=document_log_repository,
        artifact_first_retrieval_service=artifact_first_retrieval_service,
    )
    return BenchmarkServices(
        artifact_first_retrieval_service=artifact_first_retrieval_service,
        rag_answer_service=rag_answer_service,
    )


async def _run_question(
    *,
    question: BenchmarkQuestion,
    services: BenchmarkServices,
    mode: str,
    top_k: int,
    candidate_k: int,
    use_graph: bool,
) -> BenchmarkRunResult:
    retrieved_labels: list[str] = []
    matched_labels: list[str] = []
    recall_at_5 = False
    recall_at_10 = False
    precision_at_5 = 0.0
    mrr = 0.0
    wrong_document: bool | None = None
    wrong_row: bool | None = None
    wrong_article: bool | None = None
    answer: str | None = None
    citation_count: int | None = None
    answer_matches_expected: bool | None = None
    error: str | None = None

    try:
        artifact_result = await services.artifact_first_retrieval_service.retrieve(
            query=question.question,
            top_k=top_k,
            candidate_k=candidate_k,
            use_graph=use_graph,
        )
        retrieved_labels.extend(_labels_for_artifact(artifact) for artifact in artifact_result.selected_artifacts)
        chunk_response = artifact_result.chunk_response
        if chunk_response is not None:
            for result in chunk_response.results:
                retrieved_labels.extend(_labels_for_result(result))
        retrieved_labels = _dedupe(_flatten(retrieved_labels))

        expected = set(question.expected_evidence or [])
        matched_labels = [label for label in retrieved_labels if _matches_expected(label, expected)]
        recall_at_5 = bool(expected and any(_matches_expected(label, expected) for label in retrieved_labels[:5]))
        recall_at_10 = bool(expected and any(_matches_expected(label, expected) for label in retrieved_labels[:10]))
        precision_at_5 = (
            sum(1 for label in retrieved_labels[:5] if _matches_expected(label, expected)) / 5.0
            if expected
            else 0.0
        )
        mrr = _reciprocal_rank(retrieved_labels, expected)

        top_metadata = _top_result_metadata(artifact_result)
        if question.expected_doc_code is not None:
            actual_doc_code = _extract_doc_code(top_metadata)
            wrong_document = bool(actual_doc_code and not _doc_code_matches(question.expected_doc_code, actual_doc_code))
        if question.expected_row_index is not None:
            actual_row_index = _extract_row_index(top_metadata)
            wrong_row = bool(actual_row_index and actual_row_index != question.expected_row_index)
        if question.expected_article_number is not None:
            actual_article_number = _extract_article_number(top_metadata)
            wrong_article = bool(actual_article_number and actual_article_number != question.expected_article_number)

        if mode in {"answer", "both"}:
            answer_response = await services.rag_answer_service.answer(
                query=question.question,
                session_id=None,
                top_k=top_k,
                candidate_k=candidate_k,
                current_user=None,
                document_ids=None,
                session_context=None,
                memory_context=None,
                session_summary=None,
                answer_mode="hybrid",
                answer_style="detailed",
                max_context_chars=6000,
                use_graph=use_graph,
                retrieval_enrichment_enabled=False,
                query_intent_rules=None,
            )
            answer = answer_response.answer
            citation_count = len(answer_response.citations)
            if question.expected_answer:
                answer_matches_expected = _answer_matches(answer, question.expected_answer)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return BenchmarkRunResult(
        question=question,
        retrieved_labels=retrieved_labels,
        matched_labels=matched_labels,
        recall_at_5=recall_at_5,
        recall_at_10=recall_at_10,
        precision_at_5=precision_at_5,
        mrr=mrr,
        wrong_document=wrong_document,
        wrong_row=wrong_row,
        wrong_article=wrong_article,
        answer=answer,
        citation_count=citation_count,
        answer_matches_expected=answer_matches_expected,
        error=error,
    )


def _load_questions(path: Path, *, limit: int | None = None) -> list[BenchmarkQuestion]:
    raw_rows = _read_jsonl(path)
    if limit is not None:
        raw_rows = raw_rows[:limit]
    questions: list[BenchmarkQuestion] = []
    for row in raw_rows:
        question = str(row.get("question") or "").strip()
        if not question:
            continue
        questions.append(
            BenchmarkQuestion(
                question=question,
                question_type=str(row.get("question_type") or "unknown").strip(),
                expected_answer=(str(row["expected_answer"]).strip() if row.get("expected_answer") is not None else None),
                expected_doc_code=(str(row["expected_doc_code"]).strip() if row.get("expected_doc_code") is not None else None),
                expected_evidence=_normalize_expected_evidence(row.get("expected_evidence")),
                expected_row_index=(str(row["expected_row_index"]).strip() if row.get("expected_row_index") is not None else None),
                expected_article_number=(str(row["expected_article_number"]).strip() if row.get("expected_article_number") is not None else None),
            )
        )
    return questions


def _load_expected_evidence(path: Path) -> dict[str, list[str]]:
    expected_by_type: dict[str, list[str]] = {}
    for row in _read_jsonl(path):
        question_type = str(row.get("question_type") or "").strip()
        if not question_type:
            continue
        evidence = _normalize_expected_evidence(row.get("expected_evidence"))
        if evidence:
            expected_by_type[question_type] = evidence
    return expected_by_type


def _merge_expected_evidence(
    question: BenchmarkQuestion,
    expected_by_type: dict[str, list[str]],
) -> BenchmarkQuestion:
    if question.expected_evidence:
        return question
    return BenchmarkQuestion(
        question=question.question,
        question_type=question.question_type,
        expected_answer=question.expected_answer,
        expected_doc_code=question.expected_doc_code,
        expected_evidence=expected_by_type.get(question.question_type, []),
        expected_row_index=question.expected_row_index,
        expected_article_number=question.expected_article_number,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing benchmark file: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _normalize_expected_evidence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return _dedupe([str(item).strip() for item in values if str(item).strip()])


def _labels_for_artifact(artifact) -> list[str]:
    labels = {str(getattr(artifact, "artifact_type", "") or "")}
    artifact_type = str(getattr(artifact, "artifact_type", "") or "")
    if artifact_type == "document_profile":
        labels.update({"document_summary_artifact", "document_header"})
    elif artifact_type == "person_assignment_artifact":
        labels.update({"assignment_artifact", "entity_profile_artifact"})
    elif artifact_type == "table_row_artifact":
        labels.update({"table_row", "table_artifact"})
    elif artifact_type == "policy_rule_artifact":
        labels.update({"legal_clause", "legal_evidence_artifact"})
    elif artifact_type == "procedure_artifact":
        labels.update({"deadline_artifact"})
    return sorted(label for label in labels if label)


def _labels_for_result(result) -> list[str]:
    metadata = dict(getattr(result, "metadata", None) or {})
    labels = {str(metadata.get("chunk_type") or "")}
    artifact_type = str(metadata.get("artifact_type") or "")
    if artifact_type:
        labels.add(artifact_type)
    if metadata.get("table_title"):
        labels.update({"table_title", "table_artifact"})
    if metadata.get("table_headers"):
        labels.add("table_header")
    if metadata.get("article_number"):
        labels.update({"legal_clause", "legal_evidence_artifact"})
    if metadata.get("row_index") is not None:
        labels.update({"table_row", "table_artifact"})
    if metadata.get("document_code") or metadata.get("doc_code"):
        labels.update({"document_header", "document_summary_artifact"})
    return sorted(label for label in labels if label)


def _flatten(values: list[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        if isinstance(value, str):
            flattened.append(value)
        elif isinstance(value, list):
            flattened.extend(str(item) for item in value if str(item).strip())
        elif value is not None:
            flattened.append(str(value))
    return flattened


def _matches_expected(label: str, expected: set[str]) -> bool:
    expected_casefold = {item.casefold() for item in expected}
    label_casefold = label.casefold()
    if label_casefold in expected_casefold:
        return True
    aliases = EVIDENCE_ALIASES.get(label_casefold)
    if not aliases:
        return False
    return any(alias.casefold() in expected_casefold for alias in aliases)


def _reciprocal_rank(labels: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    for index, label in enumerate(labels, start=1):
        if _matches_expected(label, expected):
            return 1.0 / index
    return 0.0


def _top_result_metadata(artifact_result) -> dict[str, Any]:
    if getattr(artifact_result, "selected_artifacts", None):
        artifact = artifact_result.selected_artifacts[0]
        metadata = dict(getattr(artifact, "structured_data", None) or {})
        metadata.update(getattr(artifact, "normalized_identifiers", None) or {})
        metadata["artifact_type"] = getattr(artifact, "artifact_type", None)
        metadata["document_id"] = str(getattr(artifact, "document_id", "") or "")
        return metadata
    chunk_response = getattr(artifact_result, "chunk_response", None)
    if chunk_response is None or not getattr(chunk_response, "results", None):
        return {}
    return dict(chunk_response.results[0].metadata or {})


def _extract_doc_code(metadata: dict[str, Any]) -> str | None:
    for key in ("doc_code", "document_code", "id_vb", "ky_hieu"):
        value = metadata.get(key)
        if value:
            return str(value)
    identifiers = metadata.get("identifiers")
    if isinstance(identifiers, list) and identifiers:
        return str(identifiers[0])
    return None


def _extract_row_index(metadata: dict[str, Any]) -> str | None:
    for key in ("row_index", "row_start", "row_number"):
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return None


def _extract_article_number(metadata: dict[str, Any]) -> str | None:
    for key in ("article_number", "clause_number", "point_number"):
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return None


def _doc_code_matches(expected: str, actual: str) -> bool:
    expected_norm = " ".join(str(expected or "").split()).casefold()
    actual_norm = " ".join(str(actual or "").split()).casefold()
    return expected_norm == actual_norm or expected_norm in actual_norm or actual_norm in expected_norm


def _answer_matches(actual: str, expected: str) -> bool:
    actual_norm = " ".join((actual or "").split()).casefold()
    expected_norm = " ".join((expected or "").split()).casefold()
    return bool(actual_norm and expected_norm and expected_norm in actual_norm)


def _retrieval_row(result: BenchmarkRunResult) -> dict[str, Any]:
    return {
        "question": result.question.question,
        "question_type": result.question.question_type,
        "expected_evidence": json.dumps(result.question.expected_evidence or [], ensure_ascii=False),
        "retrieved_labels": json.dumps(result.retrieved_labels, ensure_ascii=False),
        "matched_labels": json.dumps(result.matched_labels, ensure_ascii=False),
        "recall_at_5": result.recall_at_5,
        "recall_at_10": result.recall_at_10,
        "precision_at_5": f"{result.precision_at_5:.3f}",
        "mrr": f"{result.mrr:.3f}",
        "wrong_document": result.wrong_document,
        "wrong_row": result.wrong_row,
        "wrong_article": result.wrong_article,
        "error": result.error,
    }


def _answer_row(result: BenchmarkRunResult) -> dict[str, Any]:
    return {
        "question": result.question.question,
        "question_type": result.question.question_type,
        "expected_answer": result.question.expected_answer,
        "answer": result.answer,
        "citation_count": result.citation_count,
        "answer_matches_expected": result.answer_matches_expected,
        "error": result.error,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown_report(path: Path, *, results: list[BenchmarkRunResult], questions: int) -> None:
    if not results:
        path.write_text("# Retrieval Evaluation\n\nNo benchmark rows.\n", encoding="utf-8")
        return
    valid_results = [result for result in results if result.error is None]
    recall_at_5 = mean([1.0 if result.recall_at_5 else 0.0 for result in valid_results]) if valid_results else 0.0
    recall_at_10 = mean([1.0 if result.recall_at_10 else 0.0 for result in valid_results]) if valid_results else 0.0
    mrr = mean([result.mrr for result in valid_results]) if valid_results else 0.0
    precision_at_5 = mean([result.precision_at_5 for result in valid_results]) if valid_results else 0.0
    hit_rate = sum(1 for result in valid_results if result.recall_at_5) / len(valid_results) if valid_results else 0.0
    lines = [
        "# Retrieval Evaluation",
        "",
        f"- Questions: {questions}",
        f"- Successful rows: {len(valid_results)}",
        f"- Errors: {len(results) - len(valid_results)}",
        f"- Recall@5: {recall_at_5:.3f}",
        f"- Recall@10: {recall_at_10:.3f}",
        f"- MRR: {mrr:.3f}",
        f"- Precision@5: {precision_at_5:.3f}",
        f"- Hit Rate: {hit_rate:.3f}",
        "",
        "## Notes",
        "",
        "- Evidence labels are matched deterministically against chunk types and artifact types.",
        "- Answer metrics are emitted only when `--mode answer` or `--mode both` is used.",
        "- This report is intended to be regenerated after re-ingest / re-index runs.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_error_report(path: Path, *, results: list[BenchmarkRunResult]) -> None:
    failures = [result for result in results if result.error]
    lines = [
        "# Retrieval Error Analysis",
        "",
        f"- Total rows: {len(results)}",
        f"- Failed rows: {len(failures)}",
        "",
    ]
    if not failures:
        lines.append("No benchmark errors.")
    else:
        for result in failures:
            lines.append(f"- {result.question.question_type}: {result.question.question} -> {result.error}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
