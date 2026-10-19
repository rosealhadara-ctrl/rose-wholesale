# Adds gomltha.com and www.gomltha.com to the rose-wholesale Railway service and prints the DNS records.
# Touches only the rose-wholesale service. The retail store (yaseer-loyalty-trial) is not linked or changed.
$ErrorActionPreference = "Stop"
$root = "F:\Rose-Claude"
$work = Join-Path $root "deploy\gomla-link"
$logDir = Join-Path $root "deploy\logs"
$status = Join-Path $logDir "gomltha-domain.txt"
New-Item -ItemType Directory -Force -Path $work, $logDir | Out-Null
Set-Content -Path $status -Value ("START " + (Get-Date -Format s)) -Encoding UTF8
function Say($text) { Write-Host $text; Add-Content -Path $status -Value $text -Encoding UTF8 }
function Rw([string[]]$a) {
  $ErrorActionPreference = "Continue"
  $o = (& railway @a 2>&1 | Out-String)
  $code = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  return @{ out = $o.Trim(); code = $code }
}
try {
  Set-Location $work
  $r = Rw @("link", "-p", "yaseer-loyalty-trial", "-e", "production", "-s", "rose-wholesale")
  if ($r.code) { throw ("railway link failed: " + $r.out) }
  Say "LINKED rose-wholesale"
  foreach ($d in @("gomltha.com", "www.gomltha.com")) {
    Say "== $d"
    $r = Rw @("domain", $d)
    Say $r.out
  }
  Say "DOMAIN DONE"
} catch {
  Say ("DOMAIN FAILED: " + $_.Exception.Message)
}
Write-Host ""
Write-Host "Status file: $status"
