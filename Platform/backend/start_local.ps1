$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $workspace
$backend = Join-Path $workspace 'backend'
$frontend = Join-Path $workspace 'frontend'
$apiPort = 8792
$env:EPFO_API_PORT = [string]$apiPort
$sourceEnv = Join-Path $backend 'config\source_data.env'
if (Test-Path -LiteralPath $sourceEnv) {
  foreach ($line in Get-Content -LiteralPath $sourceEnv) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and -not $line.TrimStart().StartsWith('#')) {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2])
    }
  }
}
function Stop-LocalProcess([string]$pattern) {
  $targets = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like $pattern
  }
  foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

# Stop only this project's workers; unrelated projects are left untouched.
$apiTargets = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python(?:\.exe)?$' -and $_.CommandLine -match ' -m Platform\.backend\.api_service(?:\s|$)'
}
foreach ($target in $apiTargets) {
  Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}
Stop-LocalProcess (('*' + $frontend + '*vite*').Replace('/', '\'))
Start-Sleep -Milliseconds 500

# Choose the port only after stale instances of this project have stopped.
$uiPort = 0
foreach ($candidate in 5201..5210) {
  if (-not (Get-NetTCPConnection -State Listen -LocalPort $candidate -ErrorAction SilentlyContinue)) {
    $uiPort = $candidate
    break
  }
}
if ($uiPort -eq 0) { throw 'No free local UI port was found in 5201-5210.' }

$api = Start-Process -FilePath 'python' -ArgumentList '-m', 'Platform.backend.api_service' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
$ui = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev', '--', '--port', $uiPort -WorkingDirectory $frontend -WindowStyle Hidden -PassThru

$deadline = (Get-Date).AddSeconds(20)
do {
  Start-Sleep -Milliseconds 500
  $apiReady = $false
  $uiReady = $false
  try { $apiReady = (Invoke-RestMethod ('http://127.0.0.1:' + $apiPort + '/health')).status -in @('ok', 'degraded') } catch {}
  try { $uiReady = (Invoke-WebRequest ('http://127.0.0.1:' + $uiPort + '/') -UseBasicParsing).StatusCode -eq 200 } catch {}
} while ((!$apiReady -or !$uiReady) -and (Get-Date) -lt $deadline)

if (!$apiReady) { throw ('Local API did not become ready on port ' + $apiPort + '.') }
if (!$uiReady) { throw ('Desktop UI did not become ready on port ' + $uiPort + '.') }

Write-Output ('API: http://127.0.0.1:' + $apiPort)
Write-Output ('Desktop UI: http://127.0.0.1:' + $uiPort)
Write-Output ('API process: ' + $api.Id)
Write-Output ('UI launcher process: ' + $ui.Id)
