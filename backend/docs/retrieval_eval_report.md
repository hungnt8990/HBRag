# Retrieval Eval Report

Status: framework created. Run:

```powershell
python scripts/maintenance/chunking_benchmark.py --mode retrieval --benchmark-dir docs --output-dir docs
```

Metrics emitted:

- Recall@5
- Recall@10
- MRR
- Precision@5
- Hit Rate
- Wrong Document Rate
- Wrong Row Rate
- Wrong Article Rate
