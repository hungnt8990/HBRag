@echo off
setlocal
REM ============================================================================
REM  JOB 1/2: Dong bo PostgreSQL (raw) + Elasticsearch (da lam sach + ACL nen).
REM  KHONG embed Qdrant -> chay duoc ca khi model embedding chet.
REM  Quet xong dung im cho DOFFICE_JOB_INTERVAL giay roi quet lai (incremental).
REM  Job con lai (Qdrant): run_qdrant.bat.
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM MODE 1: vai van ban le -> set "DOFFICE_JOB_ID_VB=1068586"
REM MODE 2: theo don vi (id_dv) - chi dung khi ID_VB rong. Danh sach don vi:
REM   251  EVNCPC - Tong cong ty (GOC)         122  Truong Cao dang dien luc mien Trung
REM   252  Ban QLDA Luoi dien mien Trung       254  CTCP dau tu Dien luc 3
REM   256  Cong ty CNTT DLMT (CPCIT)           257  Cong ty Thi nghiem dien mien Trung
REM   258  Cong ty Tu van Dien mien Trung      274  Cong ty Dich vu dien luc mien Trung
REM   259  Dien luc Binh Dinh   261 Dak Lak    262 Gia Lai   263 Kon Tum   264 Phu Yen
REM   265 Quang Binh  266 Quang Nam  267 Quang Ngai  268 Quang Tri  269 Hue  270 Dak Nong
REM   385 Da Nang  386 CTCP DL Khanh Hoa  1804 Cty DL Khanh Hoa  486/487 TT DLMT
REM   (Tong 335 don vi - tra bang dm_don_vi neu can id con.)
set "DOFFICE_JOB_DON_VI=256"

REM MODE 3: TAT CA -> de trong ca ID_VB lan DON_VI

REM Quet lai dinh ky (giay). 0 = chay 1 lan roi thoat. Mac dinh 300 (5 phut).
set "DOFFICE_JOB_INTERVAL=300"

REM Tham so phu
set "DOFFICE_JOB_BATCH_SIZE=200"
set "DOFFICE_JOB_WORKERS=8"
set "DOFFICE_JOB_LIMIT="

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM --skip-qdrant: chi PG + ES. Tham so dong lenh (%*) ghi de.
"%PY%" -m jobs.doffice_sync.run_unified --skip-qdrant %*

popd
endlocal
pause
