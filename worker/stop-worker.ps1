$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'worker.pid'
if (!(Test-Path -LiteralPath $pidFile)) { Write-Host 'No worker PID recorded.'; exit 0 }
$workerPid = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $workerPid"
$expected = Join-Path $PSScriptRoot 'server.py'
if ($process -and $process.CommandLine.Contains($expected)) { Stop-Process -Id $workerPid; Write-Host 'LiDAR worker stopped.' }
else { Write-Host 'Recorded worker is no longer running.' }
