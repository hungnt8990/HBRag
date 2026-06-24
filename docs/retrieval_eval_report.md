# Retrieval Evaluation Report

Status: not executed in this container.

## Scope

This report is intended to compare retrieval quality across:

- vector search
- keyword search
- hybrid search
- reranked search
- artifact-first retrieval

## Metrics To Record

- Recall@5
- Recall@10
- MRR
- Precision@5
- Hit Rate
- Wrong Document Rate
- Wrong Row Rate
- Wrong Article Rate

## Notes

The codebase already has the plumbing needed to collect retrieval logs and run the above evaluation once a benchmark corpus is wired in.

