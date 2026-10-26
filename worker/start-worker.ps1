$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '.runtime/python.exe'
if (!(Test-Path -LiteralPath $runtime)) { throw 'The processing runtime is missing. Install conda-forge python=3.12 libpdal-core gdal in .runtime first.' }
try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health'; if ($health.version -eq 2) { Write-Host 'LiDAR worker is already running.'; exit 0 } } catch {}
$worker = Join-Path $PSScriptRoot 'server.py'
$process = Start-Process -FilePath $runtime -ArgumentList @('-u', ('"' + $worker + '"')) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'worker.log') -RedirectStandardError (Join-Path $PSScriptRoot 'worker-error.log') -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'worker.pid')
Write-Host ('LiDAR processing worker started (PID ' + $process.Id + '). Keep this laptop awake until processing finishes.')
