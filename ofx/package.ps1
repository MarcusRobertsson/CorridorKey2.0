param([string]$Bundle = "$PSScriptRoot/build-zig/CorridorKey2.ofx.bundle")
$ErrorActionPreference = "Stop"
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$dist = Join-Path $PSScriptRoot "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$archive = Join-Path $dist "CorridorKey2-Windows-x64.zip"
Compress-Archive -LiteralPath $bundlePath,"$PSScriptRoot/README.md","$PSScriptRoot/VALIDATION.md" -DestinationPath $archive -Force
Write-Host "Package: $archive"
