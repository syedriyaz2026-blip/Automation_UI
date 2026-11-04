$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

while ($true) {
  $apiReady = $false
  $uiReady = $false
  try { $apiReady = (Invoke-RestMethod 'http://127.0.0.1:8792/health' -TimeoutSec 5).status -in @('ok', 'degraded') } catch {}
  try { $uiReady = (Invoke-WebRequest 'http://127.0.0.1:5201/' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
  if (-not $apiReady -or -not $uiReady) {
    powershell -ExecutionPolicy Bypass -File '.\Platform\backend\start_local.ps1' | Out-Host
  }
  Start-Sleep -Seconds 30
}
