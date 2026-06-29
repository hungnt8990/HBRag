@echo off
setlocal
REM ============================================================================
REM  JOB 2/2: Chunk + embed + day Qdrant cho van ban DOffice DA co trong PG.
REM  Doc doc tho tu PostgreSQL (co qdrant_indexed != true) -> lam sach -> chunk ->
REM  embed -> Qdrant Col1 (chunks) + Col2 (docmeta) -> danh dau qdrant_indexed.
REM  Quet xong dung im cho DOFFICE_QDRANT_INTERVAL giay roi quet lai.
REM  CAN model embedding (Qwen3-Embedding-8B) song. Job con lai: run_pg_es.bat.
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM Quet lai dinh ky (giay). 0 = chay 1 lan roi thoat. Mac dinh 300 (5 phut).
set "DOFFICE_QDRANT_INTERVAL=300"

REM So worker embed. Qdrant dung CHUNG 1 client -> nhieu worker qua de dut ket noi.
REM De 4 cho on dinh (tang dan neu gateway/Qdrant chiu tai tot).
set "DOFFICE_QDRANT_WORKERS=4"

REM Lo quet PG moi vong.
set "DOFFICE_QDRANT_BATCH_SIZE=200"

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Tham so dong lenh (%*) ghi de bien moi truong.
"%PY%" -m jobs.doffice_sync.run_qdrant %*

popd
endlocal
pause
