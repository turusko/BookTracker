@echo off
setlocal
pushd "%~dp0.."
if errorlevel 1 exit /b 1

echo Deploying BookTracker with Docker...
echo Stop any locally running BookTracker app before continuing.
echo Docker data from an earlier deployment will be preserved.
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is unavailable. Start Docker Desktop with Linux containers and try again.
    goto :failed
)
docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Compose is unavailable. Install or update Docker Desktop.
    goto :failed
)

docker compose -f compose.yaml build booktracker
if errorlevel 1 goto :failed

rem Seed only an absent database, using SQLite's consistent backup API.
docker compose -f compose.yaml run --rm --no-deps -T -v "%CD%:/import:ro" --entrypoint python booktracker /import/scripts/docker_seed.py
if errorlevel 1 goto :failed

docker compose -f compose.yaml up -d --wait --wait-timeout 60 booktracker
if errorlevel 1 goto :failed

echo.
echo BookTracker is running: http://localhost:5000
echo Your Docker database persists between deployments.
echo To stop: docker compose -f compose.yaml down
echo Do not use down -v unless you want to delete the Docker database.
popd
pause
exit /b 0

:failed
echo.
echo Deployment failed. See the error above.
popd
pause
exit /b 1
