@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem HBRag full RAG data reset.
rem Deletes all document/RAG data so the project can ingest from scratch.
rem Keeps users, roles, organizations, and database schema.

set "COMPOSE_FILE=docker-compose.yml"
set "POSTGRES_USER=hbrag"
set "POSTGRES_DB=hbrag"
set "MINIO_VOLUME=hbrag_minio_data"
set "QDRANT_VOLUME=hbrag_qdrant_data"

cd /d "%~dp0.."

echo.
echo HBRag FULL reset: documents + MinIO + Qdrant
echo =============================================
echo Repo: %CD%
echo.
echo This will DELETE ALL existing RAG/document data:
echo   - PostgreSQL documents, document_files, chunks
echo   - PostgreSQL document logs, retrieval logs, chat sessions/messages/citations
echo   - PostgreSQL graph document status and graph extraction logs
echo   - All MinIO uploaded files in Docker volume: %MINIO_VOLUME%
echo   - All Qdrant vectors/collections in Docker volume: %QDRANT_VOLUME%
echo.
echo This will KEEP:
echo   - PostgreSQL users, roles, organizations
echo   - PostgreSQL schema/migrations
echo   - Neo4j volume
echo.
echo After this, upload/parse/chunk/index documents again from the UI.
echo.

where docker >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker was not found in PATH.
  exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker Compose v2 was not found. Install Docker Desktop or enable docker compose.
  exit /b 1
)

if not exist "%COMPOSE_FILE%" (
  echo ERROR: %COMPOSE_FILE% not found. Run this script from the HBRag repo or keep it under scripts\.
  exit /b 1
)

set /p "CONFIRM=Type DELETE_ALL to permanently delete all RAG/document data: "
if not "%CONFIRM%"=="DELETE_ALL" (
  echo Cancelled.
  exit /b 0
)

echo.
echo Starting PostgreSQL so document data can be cleaned...
docker compose -f "%COMPOSE_FILE%" up -d postgres
if errorlevel 1 (
  echo ERROR: Failed to start postgres.
  exit /b 1
)

set "SQL_FILE=%TEMP%\hbrag-full-reset-%RANDOM%.sql"
(
  echo \set ON_ERROR_STOP on
  echo BEGIN;
  echo.
  echo TRUNCATE TABLE retrieval_logs RESTART IDENTITY CASCADE;
  echo TRUNCATE TABLE chat_sessions RESTART IDENTITY CASCADE;
  echo TRUNCATE TABLE documents RESTART IDENTITY CASCADE;
  echo.
  echo COMMIT;
) > "%SQL_FILE%"

echo Deleting PostgreSQL document/chunk/chat/retrieval data...
docker compose -f "%COMPOSE_FILE%" exec -T postgres psql -U "%POSTGRES_USER%" -d "%POSTGRES_DB%" -f - < "%SQL_FILE%"
set "PSQL_EXIT=%ERRORLEVEL%"
del "%SQL_FILE%" >nul 2>nul
if not "%PSQL_EXIT%"=="0" (
  echo ERROR: PostgreSQL cleanup failed.
  exit /b 1
)

echo.
echo Stopping MinIO and Qdrant containers...
docker compose -f "%COMPOSE_FILE%" stop minio minio-init qdrant
if errorlevel 1 (
  echo ERROR: Failed to stop MinIO/Qdrant services.
  exit /b 1
)

echo Removing MinIO and Qdrant containers...
docker compose -f "%COMPOSE_FILE%" rm -f minio minio-init qdrant
if errorlevel 1 (
  echo ERROR: Failed to remove MinIO/Qdrant containers.
  exit /b 1
)

echo Removing MinIO and Qdrant Docker volumes...
docker volume inspect "%MINIO_VOLUME%" >nul 2>nul
if not errorlevel 1 (
  docker volume rm "%MINIO_VOLUME%"
  if errorlevel 1 (
    echo ERROR: Failed to remove %MINIO_VOLUME%.
    exit /b 1
  )
) else (
  echo Volume %MINIO_VOLUME% does not exist. Skipping.
)

docker volume inspect "%QDRANT_VOLUME%" >nul 2>nul
if not errorlevel 1 (
  docker volume rm "%QDRANT_VOLUME%"
  if errorlevel 1 (
    echo ERROR: Failed to remove %QDRANT_VOLUME%.
    exit /b 1
  )
) else (
  echo Volume %QDRANT_VOLUME% does not exist. Skipping.
)

echo.
echo Starting MinIO and Qdrant again...
docker compose -f "%COMPOSE_FILE%" up -d minio qdrant minio-init
if errorlevel 1 (
  echo ERROR: Failed to start MinIO/Qdrant services.
  exit /b 1
)

echo.
echo Done. All old RAG/document data has been deleted.
echo You can now upload, parse, chunk, and index documents again.
echo.
pause

