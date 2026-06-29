@echo off
setlocal
REM ============================================================================
REM  Job dong bo DOffice 3-DB (PG + ES BM25 + Qdrant 2 collection)
REM  Chon MODE bang bien moi truong ben duoi (bo trong = khong dung mode do).
REM  Uu tien: DOFFICE_JOB_ID_VB > DOFFICE_JOB_DON_VI > TAT CA.
REM  Co the chay tu bat ky thu muc nao (bat tu cd ve goc backend).
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM MODE 1: vai van ban le (vd: 1068586,1068587)
REM set "DOFFICE_JOB_ID_VB=1068586"

REM MODE 2: theo don vi (vd: 251,252) - chi dung khi ID_VB rong
set "DOFFICE_JOB_DON_VI=256"

REM MODE 3: TAT CA -> de trong ca ID_VB lan DON_VI

REM Tham so phu
set "DOFFICE_JOB_BATCH_SIZE=200"
set "DOFFICE_JOB_WORKERS=8"
set "DOFFICE_JOB_LIMIT="

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Lan dau, reset 3 DB truoc (bo REM neu muon - XOA KHONG HOI PHUC):
REM "%PY%" -m scripts.reset_all_stores --yes

REM Tham so dong lenh (%*) ghi de bien moi truong:
"%PY%" -m jobs.doffice_sync.run_unified %*

popd
endlocal
