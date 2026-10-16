# Creates the new Railway service for روز جملة (rose-wholesale) next to the current one.
# Does NOT touch the current store service or its data.
$ErrorActionPreference = "Stop"
$root = "F:\Rose-Claude"
$work = Join-Path $root "deploy\gomla-link"
$logDir = Join-Path $root "deploy\logs"
$secretDir = Join-Path $root "secrets"
$status = Join-Path $logDir "gomla-setup.txt"
$pwFile = Join-Path $secretDir "gomla-owner-password.txt"
$svc = "rose-wholesale"
$domain = "gomla.sheout-eg.com"
New-Item -ItemType Directory -Force -Path $work, $logDir, $secretDir | Out-Null
Set-Content -Path $status -Value ("START " + (Get-Date -Format s)) -Encoding UTF8
function Say($text) { Write-Host $text; Add-Content -Path $status -Value $text -Encoding UTF8 }
function Rw([string[]]$a) {
  $ErrorActionPreference = "Continue"
  $o = (& railway @a 2>&1 | Out-String)
  $code = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  return @{ out = $o.Trim(); code = $code }
}
function Code($url) {
  try { return [int](Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 20).StatusCode }
  catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } return 0 }
}
try {
  Set-Location $work
  Say ("railway version: " + (Rw @("--version")).out)

  Say "== service"
  $r = Rw @("link", "-p", "yaseer-loyalty-trial", "-e", "production", "-s", $svc)
  if ($r.code) {
    Say "service not found, creating it from GitHub repo rosealhadara-ctrl/rose-wholesale"
    $r = Rw @("link", "-p", "yaseer-loyalty-trial", "-e", "production")
    if ($r.code) { throw ("railway link failed: " + $r.out) }
    $r = Rw @("add", "--service", $svc, "--repo", "rosealhadara-ctrl/rose-wholesale")
    Say ("add: " + $r.out)
    if ($r.code) { throw "could not create the service (Railway may need access to the new GitHub repo)" }
    $r = Rw @("link", "-p", "yaseer-loyalty-trial", "-e", "production", "-s", $svc)
    if ($r.code) { throw ("link to new service failed: " + $r.out) }
  }
  Say "SERVICE_OK"

  Say "== volume /data"
  $r = Rw @("volume", "list")
  if ($r.out -match "/data") { Say "volume already there" }
  else {
    $r = Rw @("volume", "add", "--mount-path", "/data")
    Say ("volume: " + $r.out)
  }

  Say "== variables"
  if (Test-Path $pwFile) { $pw = (Get-Content $pwFile -Raw).Trim() }
  else {
    $chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $pw = -join (1..16 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Set-Content -Path $pwFile -Value $pw -Encoding ASCII
  }
  $r = Rw @("variables", "--skip-deploys",
    "--set", "WHOLESALE_ENV=production",
    "--set", "WHOLESALE_COOKIE_SECURE=1",
    "--set", "WHOLESALE_DB_PATH=/data/wholesale.db",
    "--set", "WHOLESALE_OWNER_USERNAME=rose",
    "--set", "WHOLESALE_OWNER_DISPLAY_NAME=روز",
    "--set", ("WHOLESALE_OWNER_PASSWORD=" + $pw))
  if ($r.code) { Say ("variables FAILED (exit " + $r.code + ")") } else { Say "variables set (owner password saved in $pwFile)" }

  Say "== domains"
  $r = Rw @("domain")
  Say ("railway domain: " + $r.out)
  $r = Rw @("domain", $domain)
  Say ("custom domain: " + $r.out)

  Say "== deploy"
  $r = Rw @("redeploy", "-y")
  Say ("redeploy: " + $r.out)
  $r = Rw @("domain")
  $m = [regex]::Match($r.out, "https://[a-z0-9\-\.]+\.up\.railway\.app")
  if ($m.Success) {
    $url = $m.Value
    Say ("app url: " + $url)
    Say "waiting for the app to answer (up to 15 min)"
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
      if ((Code "$url/api/health") -eq 200) { $ok = $true; break }
      Start-Sleep -Seconds 15
    }
    if ($ok) {
      Say ("HEALTH: " + (Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 20).Content)
      Say ("trader page: " + (Code "$url/wholesale/"))
      Say ("staff login page: " + (Code "$url/staff-login/"))
      Say ("admin api protected (401 expected): " + (Code "$url/api/admin/wholesale/summary"))
    } else { Say "APP NOT ANSWERING YET" }
  }
  Say ("custom domain check: " + (Code "https://$domain/api/health"))
  Say "SETUP DONE"
} catch {
  Say ("SETUP FAILED: " + $_.Exception.Message)
}
Write-Host ""
Write-Host "Status file: $status"
