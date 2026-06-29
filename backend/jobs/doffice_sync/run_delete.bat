@echo off
setlocal
REM ============================================================================
REM  XOA van ban DOffice khoi CA 3 DB (PostgreSQL + Elasticsearch + Qdrant)
REM  Cau hinh che do xoa ben duoi. Uu tien: DEL_ID_VB > DEL_DON_VI.
REM  XOA KHONG HOI PHUC - mac dinh se HOI xac nhan (go 'yes') truoc khi xoa.
REM  Co the chay tu bat ky thu muc nao (bat tu cd ve goc backend).
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM === CHE DO 1: xoa theo VAN BAN (id_vb, cach nhau dau cach hoac phay) ===
REM    Vi du: set "DOFFICE_DEL_ID_VB=1068586 1479029"
set "DOFFICE_DEL_ID_VB="

REM === CHE DO 2: xoa theo DON VI (don_vi_list - KHOP voi luc sync) - chi dung khi ID_VB rong ===
REM    Vi du: set "DOFFICE_DEL_DON_VI=256"  (xoa moi VB da sync cho don vi 256)
REM    LUU Y: khop theo don vi QUAN LY/NHAN van ban (giong --don-vi luc sync), KHONG
REM    phai don vi ban hanh. VD VB do 251 ban hanh gui toi 256 -> thuoc ca don vi 256.
REM    Danh sach don vi (id_dv):
REM      251  EVNCPC - Tong cong ty (GOC, bao gom moi don vi con)
REM      122  Truong Cao dang dien luc mien Trung
REM      252  Ban QLDA Luoi dien mien Trung      254  CTCP dau tu Dien luc 3
REM      256  Cong ty CNTT DLMT (CPCIT)          257  Cong ty Thi nghiem dien mien Trung
REM      258  Cong ty Tu van Dien mien Trung     274  Cong ty Dich vu dien luc mien Trung
REM      259  Dien luc Binh Dinh                 261  Dien luc Dak Lak
REM      262  Dien luc Gia Lai                   263  Dien luc Kon Tum
REM      264  Dien luc Phu Yen                   265  Dien luc Quang Binh
REM      266  Dien luc Quang Nam                 267  Dien luc Quang Ngai
REM      268  Dien luc Quang Tri                 269  Dien luc Hue
REM      270  Dien luc Dak Nong                  385  Dien luc Da Nang
REM      386  CTCP Dien luc Khanh Hoa            1804 Cong ty Dien luc Khanh Hoa
REM      486  TT Cham soc khach hang DLMT        487  TT SX thiet bi do dien tu DLMT
REM      (Tong 335 don vi ke ca dien luc cap huyen - tra bang dm_don_vi neu can id con.)
set "DOFFICE_DEL_DON_VI=256"

REM Bo qua xac nhan (1 = xoa luon, khong hoi). De trong = hoi 'yes' truoc khi xoa.
set "DOFFICE_DEL_YES="

REM Ve thu muc goc backend (2 cap tren so voi file .bat)
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Tham so dong lenh (%*) ghi de bien moi truong (vd: run_delete.bat --id-vb 1068586 --yes)
"%PY%" -m jobs.doffice_sync.run_delete %*

popd
endlocal
pause
