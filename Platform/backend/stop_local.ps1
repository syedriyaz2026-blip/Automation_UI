$ErrorActionPreference = 'SilentlyContinue'

function Stop-LocalProcess([string]$pattern) {
  $targets = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like $pattern
  }
  foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
    Write-Output ('Stopped process ' + $target.ProcessId)
  }
}

Stop-LocalProcess '*api_service.py*'
$workspace = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $workspace
$frontend = (Join-Path $workspace 'frontend').Replace('/', '\')
Stop-LocalProcess (('*' + $frontend + '*vite*'))

foreach ($port in @(8792) + (5201..5210)) {
  $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if ($listeners) {
    Write-Output ('Port ' + $port + ' is still used by another process; it was not force-terminated.')
  } else {
    Write-Output ('Port ' + $port + ' released.')
  }
}
