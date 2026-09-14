param([string]$ArchiveUrl = 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskToolRoot = Join-Path $taskRoot '.tools'
New-Item -ItemType Directory -Path $taskToolRoot -Force | Out-Null
$taskArchive = Join-Path $taskToolRoot 'ffmpeg.zip'
$taskChecksums = (Invoke-WebRequest -Uri 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/checksums.sha256').Content
if ($taskChecksums -is [byte[]]) { $taskChecksums = [Text.Encoding]::UTF8.GetString($taskChecksums) }
$taskName = [IO.Path]::GetFileName(([Uri]$ArchiveUrl).AbsolutePath)
$taskLine = ($taskChecksums -split "`n" | Where-Object { $_.Trim() -match ([regex]::Escape($taskName) + '$') } | Select-Object -First 1)
if (-not $taskLine) { throw "No checksum published for $taskName" }
$taskExpectedHash = ($taskLine.Trim() -split '\s+')[0].ToLowerInvariant()
Write-Host 'Downloading FFmpeg from yt-dlp/FFmpeg-Builds...'
Invoke-WebRequest -Uri $ArchiveUrl -OutFile $taskArchive
$taskActualHash = (Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskActualHash -ne $taskExpectedHash) { throw 'FFmpeg checksum verification failed. Run again to get a consistent release.' }
$taskExtract = Join-Path $taskToolRoot ('ffmpeg-unpack-' + [guid]::NewGuid().ToString('N'))
Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskExtract
$taskBin = Get-ChildItem -LiteralPath $taskExtract -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if (-not $taskBin) { throw 'ffmpeg.exe was not found in the archive' }
$taskDestination = Join-Path $taskToolRoot 'ffmpeg'
New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
Copy-Item -LiteralPath $taskBin.FullName -Destination (Join-Path $taskDestination 'ffmpeg.exe')
Copy-Item -LiteralPath (Join-Path $taskBin.DirectoryName 'ffprobe.exe') -Destination (Join-Path $taskDestination 'ffprobe.exe')
Write-Host "FFmpeg ready: $taskDestination"
Write-Host "Verified SHA256: $taskActualHash"
Write-Host 'Archive and upstream license files are retained in .tools.'
