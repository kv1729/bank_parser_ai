# Start the bank statement parser app (the backend also serves the web pages).
# Usage (PowerShell, from anywhere):   .\start-app.ps1            or   .\start-app.ps1 -Port 8001
# If PowerShell refuses to run scripts, use start-app.cmd instead (see docs/RUNBOOK.md).
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed or not on PATH. See docs/RUNBOOK.md, section 1." -ForegroundColor Red
    exit 1
}
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port is already in use (process $($busy[0].OwningProcess))." -ForegroundColor Yellow
    Write-Host "If the app is already running, just open http://127.0.0.1:$Port/review"
    Write-Host "Otherwise start on another port:  .\start-app.ps1 -Port 8001"
    exit 1
}

# Ignore any previously activated Python environment (e.g. the old .venv at the repo root);
# the app always uses backend/.venv, managed by uv.
Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue

Set-Location (Join-Path $repo "backend")
Write-Host "Checking dependencies (uv sync)..."
uv sync --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "uv sync failed - see the message above." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Starting on http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  Upload page : http://127.0.0.1:$Port/"
Write-Host "  Review page : http://127.0.0.1:$Port/review"
Write-Host "Keep this window open while you use the app. Press Ctrl+C to stop."
Write-Host ""
uv run uvicorn bank_parser.api.main:app --host 127.0.0.1 --port $Port
