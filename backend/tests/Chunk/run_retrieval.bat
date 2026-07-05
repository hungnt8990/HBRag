@echo off
setlocal
REM ============================================================================
REM  HOI-DAP (RAG) tren kho vector test de kiem tra chat luong chunk:
REM  truy hoi (backend app/services: build_query_embedding_text -> embed dense+sparse
REM  -> Qdrant hybrid RRF -> rerank bge) roi LLM SINH CAU TRA LOI grounded tren chunk.
REM
REM  Cach dung:
REM     run_retrieval.bat "Ke hoach von SCL nam 2026 giao cho CPCIT la bao nhieu?"
REM     run_retrieval.bat "3684/EVNCPC-KD noi ve gi?" --top-k 8
REM     run_retrieval.bat "..." --no-answer     (chi xem chunk truy hoi, khong goi LLM)
REM     run_retrieval.bat "..." --no-rerank --docmeta
REM     run_retrieval.bat            (khong tham so -> che do hoi lien tuc)
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM Ve thu muc goc backend (2 cap tren so voi tests\Chunk).
pushd "%~dp0..\.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" tests\Chunk\useChunk\retrieve.py %*

popd
endlocal
