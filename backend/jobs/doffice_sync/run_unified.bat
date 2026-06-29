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

REM MODE 2: theo don vi (id_dv) - chi dung khi ID_VB rong. Danh sach don vi:
REM   251  EVNCPC - Tong cong ty (GOC, bao gom moi don vi con)
REM   122  Truong Cao dang dien luc mien Trung
REM   252  Ban QLDA Luoi dien mien Trung      254  CTCP dau tu Dien luc 3
REM   256  Cong ty CNTT DLMT (CPCIT)          257  Cong ty Thi nghiem dien mien Trung
REM   258  Cong ty Tu van Dien mien Trung     274  Cong ty Dich vu dien luc mien Trung
REM   259  Dien luc Binh Dinh                 261  Dien luc Dak Lak
REM   262  Dien luc Gia Lai                   263  Dien luc Kon Tum
REM   264  Dien luc Phu Yen                   265  Dien luc Quang Binh
REM   266  Dien luc Quang Nam                 267  Dien luc Quang Ngai
REM   268  Dien luc Quang Tri                 269  Dien luc Hue
REM   270  Dien luc Dak Nong                  385  Dien luc Da Nang
REM   386  CTCP Dien luc Khanh Hoa            1804 Cong ty Dien luc Khanh Hoa
REM   486  TT Cham soc khach hang DLMT        487  TT SX thiet bi do dien tu DLMT
REM   (Tong 335 don vi ke ca dien luc cap huyen - tra bang dm_don_vi neu can id con.)
set "DOFFICE_JOB_DON_VI=256"

REM MODE 3: TAT CA -> de trong ca ID_VB lan DON_VI

REM Tham so phu
set "DOFFICE_JOB_BATCH_SIZE=200"
set "DOFFICE_JOB_WORKERS=4"
set "DOFFICE_JOB_LIMIT="

REM So worker rieng cho luong 3 (Qdrant/embed). Qdrant dung CHUNG 1 client cho moi
REM worker -> qua nhieu worker lam Qdrant dut ket noi (RemoteProtocolError). De 4 cho on
REM dinh (tang dan neu Qdrant chiu tai tot). De trong = bang DOFFICE_JOB_WORKERS.
set "DOFFICE_JOB_QDRANT_WORKERS=4"

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
