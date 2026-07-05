@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  TEST CHUNK van ban DOffice theo dung mo hinh chunking cua job run_pg_es.bat.
REM  Lay _source tu ES doffice_vanban (theo id_vb) -> normalize -> chunk ->
REM  (1) xuat file text .\output\<id_vb>-<ky_hieu>.txt (cac chunk theo thu tu doc);
REM  (2) EMBED cac chunk vao QDRANT SERVER that (URL/API key tu .env) tren collection
REM      TEST rieng: chunk_test_chunks + chunk_test_docmeta (KHONG dung prod hbrag_doffice_*).
REM  KHONG ghi PostgreSQL / Elasticsearch.
REM
REM  MOI LAN CHAY: buoc (2) XOA + TAO LAI 2 collection test roi luu lai tu dau.
REM  Sau do dung run_retrieval.bat de truy hoi thu, kiem tra chat luong chunk.
REM
REM  NGUON id_vb: doc tu file .\data.txt (format: id1 id2 id3 ..., cach nhau bang
REM  khoang trong; cho phep nhieu dong; dong/duoi '#' la ghi chu). Truyen tham so
REM  dong lenh se GHI DE data.txt, vi du:  run_chunk_test.bat 16939 1144287
REM
REM  Bo qua buoc embed (chi xuat text): dat  set "SKIP_EMBED=1"  truoc khi chay.
REM ============================================================================

set "PYTHONIOENCODING=utf-8"

REM (Tuy chon) ghi de API ES nguon - bo dau REM neu can:
REM set "DOFFICE_ES_URL=https://10.72.121.232:9200"
REM set "DOFFICE_ES_INDEX=doffice_vanban"
REM set "DOFFICE_ES_AUTH=Basic ZG9mZmljZTo="

REM File chua danh sach id_vb (nam cung thu muc voi file .bat nay).
set "DATA_FILE=%~dp0data.txt"

REM Ve thu muc goc backend (2 cap tren so voi tests\Chunk).
pushd "%~dp0..\.."

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Uu tien tham so dong lenh neu co, nguoc lai doc tu data.txt.
if not "%~1"=="" (
    set "IDS=%*"
) else (
    if not exist "%DATA_FILE%" (
        echo [LOI] Khong tim thay file "%DATA_FILE%".
        echo        Tao file nay va ghi id_vb dang: id1 id2 id3
        goto :done
    )
    REM Doc tat ca id tu data.txt: bo qua dong trong; '#' = ghi chu; gom nhieu dong.
    set "IDS="
    for /f "usebackq eol=# delims=" %%L in ("%DATA_FILE%") do (
        set "IDS=!IDS! %%L"
    )
    echo Doc id_vb tu: %DATA_FILE%
)

if "!IDS!"=="" (
    echo [LOI] Khong co id_vb nao de xu ly.
    goto :done
)

REM (1) Chunk -> xuat file text .\output\
echo === [1/2] Chunk -^> xuat text output ===
"%PY%" tests\Chunk\chunk_test.py !IDS!

REM (2) Embed cac chunk vao kho vector local .\data\ (xoa sach roi luu lai).
if "%SKIP_EMBED%"=="1" (
    echo.
    echo [BO QUA] SKIP_EMBED=1 -^> khong embed vao kho vector.
    goto :done
)
echo.
echo === [2/2] Embed chunk -^> Qdrant server (collection test) ===
"%PY%" tests\Chunk\useChunk\build_vector_store.py !IDS!

:done
popd
endlocal
pause
