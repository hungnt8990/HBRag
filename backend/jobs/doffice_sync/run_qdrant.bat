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

REM ============================================================================
REM  === PHAM VI EMBED (chon 1 trong 3 che do, uu tien tu tren xuong) ===
REM  MODE 1: id_vb cu the (1 hoac nhieu, ngan cach dau phay). EMBED LAI ke ca da indexed
REM          -> dung de chay thu / nap lai dung van ban. De TRONG neu khong dung.
REM  MODE 2: theo don vi (chi dung khi ID_VB rong).
REM  MODE 3: TAT CA -> de TRONG ca ID_VB lan DON_VI.
REM ============================================================================

REM MODE 1: vai van ban le -> vd set "DOFFICE_QDRANT_ID_VB=1068586,1479034"
set "DOFFICE_QDRANT_ID_VB="

REM MODE 2: don vi QUAN LY (don_vi_list trong ACL) — giong --don-vi cua run_pg_es. Nhieu don vi
REM   ngan cach dau phay. CHI dung khi ID_VB rong.
REM   251  EVNCPC - Tong cong ty (GOC)         122  Truong Cao dang dien luc mien Trung
REM   252  Ban QLDA Luoi dien mien Trung       254  CTCP dau tu Dien luc 3
REM   256  Cong ty CNTT DLMT (CPCIT)           257  Cong ty Thi nghiem dien mien Trung
REM   258  Cong ty Tu van Dien mien Trung      274  Cong ty Dich vu dien luc mien Trung
REM   259 Binh Dinh  261 Dak Lak  262 Gia Lai  263 Kon Tum  264 Phu Yen  265 Quang Binh
REM   266 Quang Nam  267 Quang Ngai  268 Quang Tri  269 Hue  270 Dak Nong  385 Da Nang
set "DOFFICE_QDRANT_DON_VI="

REM So worker embed (chi dung khi KHONG tuan tu). Qdrant dung CHUNG 1 client.
set "DOFFICE_QDRANT_WORKERS=1"

REM TUAN TU (1 = mac dinh, NEN giu): xu ly 1 van ban/lan, embed TUNG chunk, KHONG song song
REM (tranh gay API gateway embedding). Hien dashboard cap nhat TAI CHO: cot trai tien do +
REM van ban dang chay + embed tung chunk + vai van ban gan day; cot phai O liet ke van ban
REM NHIEU CHUNK (>nguong). 0 = song song (nhanh hon nhung de gay gateway — chi dung khi gateway khoe).
set "DOFFICE_QDRANT_SEQUENTIAL=1"

REM Nguong "nhieu chunk" -> o "view" + chunks_big.log (mac dinh 100).
set "DOFFICE_QDRANT_BIG_CHUNK=100"

REM Nguong BO QUA: van ban > so chunk nay se KHONG embed va KHONG danh dau PG (giu pending
REM de xu ly/danh gia sau). Mac dinh 500. 0 = khong gioi han (embed het).
set "DOFFICE_QDRANT_MAX_CHUNK=500"

REM Chi xu ly toi da N van ban roi dung (de TEST vai van ban truoc). De trong = chay het.
set "DOFFICE_QDRANT_LIMIT="

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
