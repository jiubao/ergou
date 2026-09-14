$taskRoot = Split-Path $PSScriptRoot -Parent
uv run --project (Join-Path $taskRoot 'apps\services') ergou doctor
