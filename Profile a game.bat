@echo off
setlocal enabledelayedexpansion
title 7800 thread profiler
rem ---------------------------------------------------------------------------
rem  Profile a threaded-code game while you play it.
rem
rem  Double-click to profile Karateka, or drag any .a78 onto this file.
rem
rem  MAME opens as normal -- sound, full speed, playable. Play for a minute or
rem  two and GET INTO A FIGHT, because that is the part nobody has measured. A
rem  script can drive the game as far as its title screen and no further, and
rem  everything measured that way says 93% of the interpreter's work is an
rem  empty delay loop. Whether that is also true during a fight decides whether
rem  the game can be sped up by editing a few numbers or only by rewriting it.
rem
rem  Then close MAME. The profile is written and read back automatically, and
rem  the report stays on screen.
rem
rem  Nothing is modified: the profiler watches the interpreter's own writes to
rem  its thread pointer, so the game runs exactly as it would otherwise.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "PROBE=%HERE%probes\threadprof.lua"
set "FORTH=%HERE%tools\forth.py"
set "LOG=%HERE%threadprof.log"
set "BIOS=%HERE%..\bios"

if not exist "%PROBE%" (
  echo Cannot find probes\threadprof.lua next to this batch file.
  echo Keep this file in the a7800-toolkit folder.
  goto :finish
)

rem ---- the cartridge: dragged on, or Karateka by default --------------------
set "CART=%~1"
if not "%CART%"=="" goto :gotcart
set "CART=%HERE%..\Rom Library\Trebors 7800 ROM PROPack v8_17\Retail_v7_0\NTSC\Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
if exist "!CART!" goto :gotcart
echo Could not find Karateka, and nothing was dropped on this file.
echo.
echo Drag a .a78 onto this batch file, or edit CART in it.
goto :finish
:gotcart
if not exist "!CART!" (
  echo Not there: !CART!
  goto :finish
)

rem ---- MAME ----------------------------------------------------------------
set "MAME=%LOCALAPPDATA%\Programs\MAME\mame.exe"
if not exist "!MAME!" set "MAME=C:\Program Files\MAME\mame.exe"
if not exist "!MAME!" set "MAME=%HERE%..\a7800-win-v5.2\a7800.exe"
if not exist "!MAME!" (
  echo Could not find MAME. Install it, or edit MAME in this batch file.
  goto :finish
)

rem ---- Python, for reading the profile back ---------------------------------
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)

echo   cartridge: !CART!
echo   profiler : probes\threadprof.lua
echo.
echo   MAME is about to open. Play normally, and get into a fight if you can.
echo   Close MAME when you are done and the report will appear here.
echo.
pause

if exist "!LOG!" del "!LOG!"
set "A7800_PROF_LOG=!LOG!"
rem A7800_IP is the interpreter's thread pointer. $E8 is right for Karateka;
rem for another game, tools\forth.py --map prints the one it uses.
if not defined A7800_IP set "A7800_IP=0xE8"

"!MAME!" a7800 -rompath "%BIOS%" -cart "!CART!" -autoboot_script "%PROBE%" ^
    -window -skip_gameinfo

echo.
if not exist "!LOG!" (
  echo No profile was written. If MAME reported a Lua error above, the probe
  echo may not match this build; otherwise the game may not be a threaded one.
  goto :finish
)

if not defined PY (
  echo Profile written to: !LOG!
  echo No Python found to read it back. Install Python, then run:
  echo   python tools\forth.py "!CART!" --profile "!LOG!"
  goto :finish
)

echo   reading the profile back ^(this walks the whole image, give it a moment^)
echo.
%PY% "%FORTH%" "!CART!" --profile "!LOG!"

:finish
echo.
pause
endlocal
