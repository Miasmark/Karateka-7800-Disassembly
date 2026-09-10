@echo off
setlocal
REM Plays back any recorded .inp against any built ROM, with the current
REM frame number shown on screen (updates every frame, freezes when you
REM pause -- so whatever it reads at the moment you pause is the exact
REM frame to hand back for precise tracing).
REM
REM Usage:
REM   watch.bat NAME             normal speed
REM   watch.bat NAME 0.5         half speed, for tracking something precisely
REM   watch.bat NAME 0.25        quarter speed
REM
REM (name only, no .a78/.inp -- both are expected under the paths below)

if "%~1"=="" (
  echo usage: watch.bat NAME [SPEED]
  echo   e.g. watch.bat karateka-31-knockback-light
  echo   e.g. watch.bat karateka-31-knockback-light 0.5
  pause
  exit /b 1
)

pushd "%~dp0"

set "MAME=%LOCALAPPDATA%\Programs\MAME\mame.exe"
set "NAME=%~1"
set "SPEED=%~2"
if "%SPEED%"=="" set "SPEED=1"
set "ROM=..\karateka\patches\%NAME%.a78"
set "INP=%NAME%.inp"
set "BIOS=..\bios"

if not exist "%ROM%" (
  echo no such build: %ROM%
  popd
  pause
  exit /b 1
)
if not exist "..\karateka\recordings\%INP%" (
  echo no such recording: %INP%
  popd
  pause
  exit /b 1
)

"%MAME%" a7800 -cart "%ROM%" -autoboot_script "probes\framecounter.lua" -input_directory "..\karateka\recordings" -playback "%INP%" -rompath "%BIOS%" -skip_gameinfo -speed %SPEED%

popd
pause
