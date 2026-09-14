param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPort = if ($env:ERGOU_PORT) { [int]$env:ERGOU_PORT } else { 17890 }
$taskUrl = "http://127.0.0.1:$taskPort"
try {
    $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/v1/health" -TimeoutSec 2
    if ($taskHealth.protocol_version -eq 1) {
        Write-Host "Ergou is already running at $taskUrl"
        if (-not $NoBrowser) { Start-Process $taskUrl -WindowStyle Hidden }
        return
    }
} catch { }
$taskPython = Join-Path $taskRoot 'services\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'web\dist\index.html'))) { throw 'Run pnpm build first.' }
$taskRunDir = Join-Path $taskRoot '.local'
New-Item -ItemType Directory -Path $taskRunDir -Force | Out-Null
$taskOut = Join-Path $taskRunDir 'service.stdout.log'
$taskErr = Join-Path $taskRunDir 'service.stderr.log'
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-m','ergou.cli','serve','--no-token') -WorkingDirectory $taskRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr
for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
    Start-Sleep -Milliseconds 250
    if ($taskProcess.HasExited) { throw "Service failed to start. See $taskErr (the port may be in use)." }
    try {
        $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/v1/health" -TimeoutSec 1
        if ($taskHealth.protocol_version -eq 1) {
            @{pid=$taskProcess.Id; startTime=$taskProcess.StartTime.ToUniversalTime().ToString('o'); executable=$taskPython} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRunDir 'service.json')
            Write-Host "Ergou is running at $taskUrl"
            & $taskPython -m ergou.cli token
            if (-not $NoBrowser) { Start-Process $taskUrl -WindowStyle Hidden }
            return
        }
    } catch { }
}
throw "Service did not become ready. See $taskErr"
