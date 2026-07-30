# Customer 360 Portal — start script
# Launches the portal on http://localhost:8000

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  Customer 360 Portal" -ForegroundColor Cyan
Write-Host "  ===================" -ForegroundColor Cyan
Write-Host ""

$pythonOk = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonOk) { Write-Host "ERROR: python not found in PATH" -ForegroundColor Red; exit 1 }

# Preflight: the portal reads every ID from src/state.json, so a missing report_id
# means a blank embed panel later. Fail here instead, with the fix.
$statePath = Join-Path (Split-Path -Parent $root) "src\state.json"
if (-not (Test-Path $statePath)) {
    Write-Host "ERROR: src\state.json not found - run 'python deploy_all.py' first" -ForegroundColor Red
    exit 1
}
$state = Get-Content $statePath -Raw | ConvertFrom-Json
foreach ($k in @("workspace_id", "report_id", "data_agent_id")) {
    if (-not $state.$k) {
        Write-Host "ERROR: '$k' missing from src\state.json - run 'python deploy_all.py'" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  workspace : $($state.workspace_id)" -ForegroundColor DarkGray
Write-Host "  report    : $($state.report_id)" -ForegroundColor DarkGray
Write-Host "  data agent: $($state.data_agent_id)" -ForegroundColor DarkGray
Write-Host ""

# Auth: the backend uses AzureCliCredential, so 'az login' must already be done.
$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) {
    Write-Host "ERROR: not signed in - run 'az login' first" -ForegroundColor Red
    exit 1
}
Write-Host "  signed in as $($acct.user.name)" -ForegroundColor DarkGray
Write-Host ""

Write-Host "[1/2] Installing dependencies..." -ForegroundColor Yellow
Push-Location "$root\backend"
pip install -r requirements.txt -q 2>$null
Pop-Location

Write-Host "[2/2] Starting server..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Portal: http://localhost:8000" -ForegroundColor Green
Write-Host "  API:    http://localhost:8000/docs" -ForegroundColor Green
Write-Host "  Health: http://localhost:8000/api/health" -ForegroundColor Green
Write-Host ""
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

Set-Location "$root\backend"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
