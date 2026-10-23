@echo off
setlocal
for %%I in ("%~dp0..") do set "CORRIDORKEY2_ROOT=%%~fI"
set "OFX_PLUGIN_PATH=%LOCALAPPDATA%\Programs\Common\OFX\Plugins;%OFX_PLUGIN_PATH%"
set "RESOLVE_EXE=C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
if not exist "%RESOLVE_EXE%" (
  echo Resolve was not found at "%RESOLVE_EXE%".
  echo Edit RESOLVE_EXE in this launcher to match your installation.
  pause
  exit /b 1
)
start "" "%RESOLVE_EXE%"
