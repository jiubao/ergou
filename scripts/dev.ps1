$taskRoot = Split-Path $PSScriptRoot -Parent
Write-Host 'Run these commands in separate terminals from the repository root:'
Write-Host '  uv run --project services ergou serve'
Write-Host '  pnpm dev:web'
Write-Host '  pnpm dev:extension'
Write-Host 'For persistent download tests, do not use Uvicorn auto-reload.'
