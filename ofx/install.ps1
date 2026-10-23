param([string]$Bundle = "$PSScriptRoot/build/CorridorKey2.ofx.bundle")
$ErrorActionPreference = "Stop"
if (Get-Process Resolve -ErrorAction SilentlyContinue) { throw "Close DaVinci Resolve before installing" }
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
if (!(Test-Path "$bundlePath/Contents/Win64/CorridorKey2.ofx")) { throw "Build the plugin first" }
$repo = Split-Path $PSScriptRoot -Parent
if (!(Test-Path "$repo/.venv-ofx/Scripts/python.exe")) { throw "Run ofx/setup.ps1 first" }
# Per-user install through OFX_PLUGIN_PATH avoids requiring administrator access.
$destination = Join-Path $env:LOCALAPPDATA "Programs/Common/OFX/Plugins"
New-Item -ItemType Directory -Force $destination | Out-Null
Copy-Item -LiteralPath $bundlePath -Destination $destination -Recurse -Force
$existing = [Environment]::GetEnvironmentVariable("OFX_PLUGIN_PATH", "User")
$paths = @($existing -split ';' | Where-Object { $_ })
if ($paths -notcontains $destination) { $paths += $destination }
[Environment]::SetEnvironmentVariable("OFX_PLUGIN_PATH", ($paths -join ';'), "User")
[Environment]::SetEnvironmentVariable("CORRIDORKEY2_ROOT", $repo, "User")
Write-Host "Installed: $destination/CorridorKey2.ofx.bundle"
Write-Host "Start Resolve from a new sign-in session or terminal so it sees the updated environment."
Write-Host "If your Resolve build ignores OFX_PLUGIN_PATH, copy the bundle to C:/Program Files/Common Files/OFX/Plugins as administrator."
