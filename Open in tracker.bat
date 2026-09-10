@echo off
setlocal enabledelayedexpansion
title 7800 tracker
rem ---------------------------------------------------------------------------
rem  Drag a file onto this to open it in the tracker: a grid in your browser
rem  where you can type notes, hear them, and save.
rem
rem     .trk   a song            -> opens it
rem     .log   a MAME capture    -> converts it, then opens
rem     .a78   a cartridge       -> records it in MAME, then opens
rem
rem  Close the browser tab and then this window to stop. Save in the page
rem  before you do -- it writes back to the .trk.
rem
rem  A cartridge needs MAME and the 7800 BIOS. Set A7800_MAME and
rem  A7800_ROMPATH if they are not in the usual places.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "EDIT=%HERE%tools\trackeredit.py"
set "TRACKER=%HERE%tools\tracker.py"
if not defined A7800_SECONDS set "A7800_SECONDS=40"

if not exist "%EDIT%" (
  echo Cannot find tools\trackeredit.py next to this batch file.
  echo Keep this file in the a7800-toolkit folder, beside tools\.
  goto :finish
)

set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo No usable Python found. Install Python 3.7 or newer and try again.
  goto :finish
)

if "%~1"=="" (
  echo   7800 tracker
  echo.
  echo   Drag one of these onto this batch file:
  echo.
  echo      song.trk            opens it in the tracker
  echo      a7800-audio.log     converts it first
  echo      game.a78            records it in MAME first
  echo.
  echo   The grid opens in your browser. Click a cell and type; Enter commits
  echo   and steps down; Delete silences a channel; Backspace holds it.
  goto :finish
)

set "IN=%~1"
set "EXT=%~x1"
set "STEM=%~dpn1"

if /i "!EXT!"==".log" goto :fromlog
if /i "!EXT!"==".trk" goto :open
if /i "!EXT!"==".a78" goto :open
echo Not a .trk song, .log capture or .a78 cartridge.
goto :finish

:fromlog
echo Converting the capture...
%PY% "!TRACKER!" capture "!IN!" -o "!STEM!.trk"
if errorlevel 1 goto :finish
set "IN=!STEM!.trk"

:open
echo Starting the tracker. Leave this window open while you work.
echo.
%PY% "!EDIT!" "!IN!" --seconds !A7800_SECONDS!

:finish
echo.
pause
endlocal
