param([string]$Zig = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
if ($Zig) {
    $Zig = (Resolve-Path -LiteralPath $Zig).Path
    $build = Join-Path $PSScriptRoot "build-zig"
    New-Item -ItemType Directory -Force $build | Out-Null
    $env:ZIG_GLOBAL_CACHE_DIR = Join-Path $build "zig-cache"
    $env:ZIG_LOCAL_CACHE_DIR = Join-Path $build "zig-local-cache"
    # A wrapper lets CMake's ordinary compiler detection invoke zig c++.
    $wrapper = Join-Path $build "cxx.cmd"
    Set-Content -LiteralPath $wrapper -Encoding ascii -Value "@`"$Zig`" c++ -target x86_64-windows-gnu %*"
    & cmake -S "$PSScriptRoot/plugin" -B $build -G Ninja "-DCMAKE_CXX_COMPILER=$wrapper" -DCMAKE_BUILD_TYPE=Release
} else {
    $build = Join-Path $PSScriptRoot "build"
    & cmake -S "$PSScriptRoot/plugin" -B $build -G "Visual Studio 17 2022" -A x64
}
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }
& cmake --build $build --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw "OFX build failed" }
Write-Host "Bundle: $build/CorridorKey2.ofx.bundle"
