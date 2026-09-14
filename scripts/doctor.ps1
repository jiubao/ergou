$taskRoot = Split-Path $PSScriptRoot -Parent
uv run --project (Join-Path $taskRoot 'services') ergou doctor
