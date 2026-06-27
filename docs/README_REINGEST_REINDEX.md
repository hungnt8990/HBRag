# Re-ingest / Re-index Guide

1. Re-ingest or force-refresh DOffice documents so normalized table metadata, legal chunks, quality gate metadata, and artifacts are regenerated.
2. For normal uploaded documents, run the existing ingestion queue; it compiles artifacts and indexes artifacts before chunk vectors.
3. For DOffice endpoint ingestion, the service now compiles and indexes artifacts when the artifact repository/indexer dependencies are configured.
4. Run validation SQL from `final_chunking_strategy.md`.
5. Run benchmark:

```powershell
python scripts/maintenance/chunking_benchmark.py --mode both --benchmark-dir docs --output-dir docs
```
