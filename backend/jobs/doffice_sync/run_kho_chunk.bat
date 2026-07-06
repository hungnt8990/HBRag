@echo off
setlocal
REM ============================================================================
REM  JOB KHO AI 1/2: quet index kho_ai_dung_chung (ES moi 10.72.121.232) ->
REM  lam sach ocr_content -> chunk -> ghi index kho_ai_dung_chung_chunk.
REM  Checkpoint (search_after + updated_after) luu PostgreSQL -> lan sau quet tiep.
REM  ACL da nen san trong doc nguon -> copy nguyen trang, KHONG nen lai.
REM  Chunk KHONG luu PostgreSQL. Job embed Qdrant: run_kho_qdrant.bat.
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM Hien MOT bang thong tin cap nhat tai cho (tong nguon / da chunk / chua chunk / dang xu ly).
REM Xac dinh da chunk hay chua = kiem tra tren ES (khong luu Postgres): van ban nao chua co
REM chunk trong kho_ai_dung_chung_chunk thi moi chunk. Ctrl-C: dung SAU khi chunk xong van ban
REM hien tai (khong cat ngang); Ctrl-C lan 2 = buoc thoat.

REM LOOP: giay giua 2 lan quet. Mac dinh 300 = 5 phut. 0 = chay 1 lan roi thoat.
set "KHO_JOB_INTERVAL=300"

REM RESET: 9 = XOA + tao lai rong ES kho_ai_dung_chung_chunk -> chunk lai tu dau.
REM KHONG dung Qdrant (2 collection do run_kho_qdrant quan), Postgres, kho_ai_dung_chung.
REM 0 (mac dinh) = chi chunk van ban CHUA chunk.
set "KHO_JOB_RESET=0"

REM PHAM VI: chay FULL - quet TAT CA don vi. Bo loc don vi gio dung ORG_LIST (mang tat ca
REM don vi lien quan van ban: issuer + cv_den/cv_di/cv_noi_bo), KHONG con dung issuer_org_id.
REM Neu can loc lai: set "KHO_JOB_ISSUER_ORG=256,258" (van ban khop neu 1 trong cac don vi
REM nay nam trong org_list). De trong = TAT CA don vi.
set "KHO_JOB_ISSUER_ORG="

REM CHAY FULL: 1 = chunk LAI tat ca (bo kiem tra "da chunk") de backfill field moi (org_list)
REM vao chunk cu. Sau khi da chay xong 1 luot phu song, dat lai 0 de chi chunk van ban CHUA chunk
REM (khong re-chunk 16k van ban moi vong loop). 0 = chi chunk van ban chua co chunk.
set "KHO_JOB_FULL_SCAN=0"

REM Chi chunk vai doc theo field id (UUIDv7) de test. De trong = quet theo checkpoint.
set "KHO_JOB_ID="

REM Lo quet ES nguon moi vong.
set "KHO_JOB_BATCH_SIZE=200"

REM Doc > nguong chunk nay se BO QUA (log vanban_bo_qua_qua_chunk.log). 0 = khong gioi han.
set "KHO_JOB_MAX_CHUNK=500"

REM Chi xu ly toi da N doc roi dung (de TEST). De trong = chay het.
set "KHO_JOB_LIMIT="

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Tham so dong lenh (%*) ghi de bien moi truong (vd: --full-scan, --limit 5).
"%PY%" -m jobs.doffice_sync.run_kho_chunk %*

popd
endlocal
pause
