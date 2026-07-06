@echo off
setlocal
REM ============================================================================
REM  JOB KHO AI 2/2: quet kho_ai_dung_chung_chunk (chunk chua danh dau) -> embed
REM  DENSE (khong sparse) -> Qdrant hbrag_doffice_docmeta (nhanh FULL truoc) +
REM  hbrag_doffice_chunks (tung chunk sau) -> danh dau qdrant_indexed tren ES.
REM  TUAN TU 1 document/lan, KHONG da luong. Khoi tao collection neu chua co.
REM  Docmeta embed: title + signer + summary (da lam sach). Chunk: dung chunk_text
REM  co san (job 1 da lam sach). CAN model embedding (Qwen3-Embedding-8B) song.
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM Job CHAY 1 LUOT roi dung (khong loop lien tuc): quet het chunk chua danh dau theo batch,
REM embed tuan tu toi dau in ra toi do. Chay lai = chay lai file .bat.
REM Pham vi don vi da duoc loc o buoc chunk (run_kho_chunk) nen job nay embed het chunk pending.

REM RESET: 9 = XOA + TAO LAI 2 collection Qdrant (dense) + bo danh dau chunk ES de embed lai.
REM GIU nguyen ES chunk (do run_kho_chunk quan) va KHONG dung kho_ai_dung_chung.
REM Dang de 9 de CHAY FULL: re-embed toan bo (backfill field moi org_list vao payload, tranh
REM point cu mo coi). Sau khi backfill xong, dat lai 0 de chi embed chunk chua danh dau.
set "KHO_QDRANT_RESET=0"

REM Embed lai vai doc theo id_full (UUIDv7) de test — ke ca da danh dau. De trong = quet pending.
set "KHO_QDRANT_ID_FULL="

REM So id_full moi trang quet pending.
set "KHO_QDRANT_BATCH_SIZE=50"

REM So text moi request embed (1 = tung chunk, an toan gateway; tang neu gateway khoe).
set "KHO_QDRANT_EMBED_BATCH=1"

REM Chi xu ly toi da N document roi dung (de TEST). De trong = chay het.
set "KHO_QDRANT_LIMIT="

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Tham so dong lenh (%*) ghi de bien moi truong (vd: --limit 3).
"%PY%" -m jobs.doffice_sync.run_kho_qdrant %*

popd
endlocal
pause
