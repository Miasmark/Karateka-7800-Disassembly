@echo off
setlocal
REM Plays back karateka-36-loop-gate-player-slot8-fast-walk.inp at half speed
REM by default, with the current frame number shown on screen (top-left,
REM updates every frame). Pause with P -- the frame number freezes with the
REM picture, so whatever it reads at the moment you pause is the exact
REM frame to hand back for precise tracing.
REM
REM Usage:
REM   watch-fix36.bat          half speed (default, for tracking precisely)
REM   watch-fix36.bat 1        normal speed
REM   watch-fix36.bat 0.25     quarter speed

pushd "%~dp0"

set "MAME=%LOCALAPPDATA%\Programs\MAME\mame.exe"
set "ROM=..\karateka\patches\karateka-36-loop-gate-player-slot8-fast-walk.a78"
set "INP=karateka-36-loop-gate-player-slot8-fast-walk.inp"
set "BIOS=..\bios"
set "SPEED=%~1"
if "%SPEED%"=="" set "SPEED=0.5"

"%MAME%" a7800 -cart "%ROM%" -autoboot_script "probes\framecounter.lua" -input_directory "..\karateka\recordings" -playback "%INP%" -rompath "%BIOS%" -skip_gameinfo -speed %SPEED%

popd
pause
