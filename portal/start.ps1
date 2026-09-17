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

# The Python helpers select the profile and reject cross-profile receipts without signing in.
$srcPath = Join-Path (Split-Path -Parent $root) "src"
$selectionJson = python -c "import json, sys; sys.path.insert(0, sys.argv[1]); from helpers import load_state, profile_dir, state_path; print(json.dumps(dict(state=load_state(), profile=str(profile_dir() or ''), path=str(state_path()))))" $srcPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: could not read the selected deployment profile" -ForegroundColor Red
    exit 1
}
$selection = $selectionJson | ConvertFrom-Json
$state = $selection.state
foreach ($k in @("workspace_id", "report_id", "data_agent_id")) {
    if (-not $state.$k) {
        Write-Host "ERROR: '$k' missing from $($selection.path) - run 'python deploy_all.py'" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  workspace : $($state.workspace_id)" -ForegroundColor DarkGray
Write-Host "  report    : $($state.report_id)" -ForegroundColor DarkGray
Write-Host "  data agent: $($state.data_agent_id)" -ForegroundColor DarkGray
Write-Host ""

# A profile uses the helpers' checked, isolated CLI identity; never fall back to the
# operator's normal az cache. No-profile startup retains its original account check.
if ($selection.profile) {
    python -c 'import sys; sys.path.insert(0, sys.argv[1]); from helpers import ensure_tenant; ensure_tenant(quiet=True)' $srcPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: isolated Azure CLI identity does not match the selected profile" -ForegroundColor Red
        exit 1
    }
    Write-Host "  profile   : $($selection.profile)" -ForegroundColor DarkGray
} else {
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) {
        Write-Host "ERROR: not signed in - run 'az login' first" -ForegroundColor Red
        exit 1
    }
    Write-Host "  signed in as $($acct.user.name)" -ForegroundColor DarkGray
}
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
