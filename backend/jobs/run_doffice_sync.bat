@echo off
REM ============================================================================
REM  Job dong bo van ban DOffice -> PostgreSQL + ES document index (KHONG Qdrant)
REM
REM  Cach dung (chay tu bat ky dau):
REM     jobs\run_doffice_sync.bat                 (incremental, resume checkpoint)
REM     jobs\run_doffice_sync.bat --full-scan     (quet lai tu dau)
REM     jobs\run_doffice_sync.bat --dry-run --limit 5
REM     jobs\run_doffice_sync.bat --id-vb 1068586 693358
REM     jobs\run_doffice_sync.bat --retry-only
REM     jobs\run_doffice_sync.bat --workers 10 --batch 200
REM
REM  Lap lich (Task Scheduler) goi thang file .bat nay, vd 2 gio/lan.
REM ============================================================================
setlocal
set "BACKEND_DIR=%~dp0.."
set "PYTHONIOENCODING=utf-8"
cd /d "%BACKEND_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Khong tim thay .venv\Scripts\python.exe trong "%BACKEND_DIR%".
    exit /b 1
)

".venv\Scripts\python.exe" "jobs\doffice_sync\run.py" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
