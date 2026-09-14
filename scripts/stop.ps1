$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPidFile = Join-Path $taskRoot '.local\service.json'
if (-not (Test-Path -LiteralPath $taskPidFile)) { Write-Host 'No script-managed service is running.'; return }
$taskSaved = Get-Content -LiteralPath $taskPidFile | ConvertFrom-Json
$taskProcess = Get-Process -Id $taskSaved.pid -ErrorAction SilentlyContinue
if ($taskProcess) {
    $taskSavedStart = ([datetime]$taskSaved.startTime).ToUniversalTime()
    if ($taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskSavedStart.Ticks -or $taskProcess.Path -ne $taskSaved.executable) { throw 'Saved process identity does not match; no process was stopped.' }
    Stop-Process -Id $taskProcess.Id
    $taskProcess.WaitForExit()
    Write-Host 'Ergou stopped. Unfinished tasks will appear as interrupted on the next start.'
}
Remove-Item -LiteralPath $taskPidFile
