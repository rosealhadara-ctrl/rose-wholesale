# Runs the app on this computer for development: installs packages into .packages
# (once), starts the server and opens the browser. Stop it with Ctrl+C.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = $null
foreach ($c in @("py -3.12", "py -3", "python")) {
  try {
    $v = Invoke-Expression "$c -c ""import sys; print(sys.version_info[:2] >= (3, 10))""" 2>$null
    if ($v -eq "True") { $py = $c; break }
  } catch {}
}
if (-not $py) { Write-Host "Python 3.10 or newer is required. Install Python 3.12 from python.org (tick 'Add python.exe to PATH')." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
Write-Host "Using: $py"
if (-not (Test-Path ".packages\fastapi")) {
  Write-Host "Installing packages (first run only)..."
  Invoke-Expression "$py -m pip install --upgrade --target .packages -r requirements.txt"
}
$env:PYTHONPATH = (Resolve-Path .packages).Path
if (-not $env:WHOLESALE_OWNER_USERNAME) { $env:WHOLESALE_OWNER_USERNAME = "owner" }
if (-not $env:WHOLESALE_OWNER_DISPLAY_NAME) { $env:WHOLESALE_OWNER_DISPLAY_NAME = "Owner" }
if (-not $env:WHOLESALE_OWNER_PASSWORD) { $env:WHOLESALE_OWNER_PASSWORD = "Local-owner-123" }  # local test database only
$port = 8100
Start-Job -ScriptBlock { param($u) Start-Sleep 4; Start-Process $u } -ArgumentList "http://127.0.0.1:$port/admin/wholesale/" | Out-Null
Write-Host ""
Write-Host "Running on http://127.0.0.1:$port  (Ctrl+C to stop)" -ForegroundColor Green
Invoke-Expression "$py -m uvicorn app.main:app --host 127.0.0.1 --port $port --reload"
