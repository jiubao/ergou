$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Push-Location $taskRoot
try {
    uv sync --project apps/services --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    uv run --project apps/services ergou doctor
    Write-Host 'If FFmpeg is missing, run scripts/install-ffmpeg.ps1, then scripts/start.ps1.'
} finally { Pop-Location }
