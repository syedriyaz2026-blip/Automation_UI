$ErrorActionPreference = 'Stop'

function Require-Env([string]$name) {
  $value = [Environment]::GetEnvironmentVariable($name)
  if ([string]::IsNullOrWhiteSpace($value)) { throw "$name is not configured." }
}

Require-Env 'PF_READONLY_DB_CONNECTION'
Require-Env 'EPFO_JWT_SECRET'
$connection = [Environment]::GetEnvironmentVariable('PF_READONLY_DB_CONNECTION')
$serverMatch = [regex]::Match($connection, '(?i)(?:^|;)SERVER=([^;]+)')
if (-not $serverMatch.Success) { throw 'PF_READONLY_DB_CONNECTION does not contain a SERVER value.' }
$server = $serverMatch.Groups[1].Value
$host = $server
$port = 1433
if ($server -match '^([^,]+),(\d+)$') { $host = $Matches[1]; $port = [int]$Matches[2] }

$tcp = Test-NetConnection -ComputerName $host -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
if (-not $tcp) { throw "SQL server $host`:$port is not reachable." }

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = $projectRoot
python -c "from Platform.backend.source_data import ReadOnlyDatabase; db=ReadOnlyDatabase(); c=db._connect(); cur=c.cursor(); cur.execute('SELECT DB_NAME()'); print('SQL login OK: '+str(cur.fetchone()[0])); c.close()"
if ($LASTEXITCODE -ne 0) { throw 'SQL login/query preflight failed.' }

Write-Output 'PF UAT preflight passed.'
Write-Output "SQL target: $host`:$port"
Write-Output "EPFO runs enabled: $([Environment]::GetEnvironmentVariable('PF_EPFO_RUNS_ENABLED'))"
