@echo off
setlocal enabledelayedexpansion
title 7800 workbench
rem ---------------------------------------------------------------------------
rem  Drag a cartridge onto this to open the workbench: what the header says,
rem  what a scan finds, and a button on each result that opens it in the right
rem  editor with the space, base and format already filled in.
rem
rem     .a78   a cartridge with a header   -> opens it
rem     .bin   a headerless dump           -> opens it, mapper guessed from size
rem
rem  The page opens in your browser. Leave this window open while you work:
rem  closing it stops the workbench and every editor it launched, which is
rem  deliberate -- an editor left holding its port looks exactly like a stale
rem  server the next time you run one.
rem
rem  Set A7800_WORKBENCH_PORT to move it off 8120. The editors it launches take
rem  the ports above 8140.
rem
rem  Everything here is drag-and-drop-able:
rem     Open workbench.bat      this -- start here with an unfamiliar cartridge
rem     Open in tracker.bat     straight to the music
rem     Render dropped file.bat straight to a WAV
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "WB=%HERE%tools\workbench.py"
if not defined A7800_WORKBENCH_PORT set "A7800_WORKBENCH_PORT=8120"

if not exist "%WB%" (
  echo Cannot find tools\workbench.py next to this batch file.
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
  echo   7800 workbench
  echo.
  echo   Drag a cartridge onto this batch file:
  echo.
  echo      game.a78            opens it
  echo      game.bin            headerless, mapper guessed from the size
  echo.
  echo   You get the header, the mapper, the vectors and the spaces, then a
  echo   scan you can run for artwork and music. Each result has a button
  echo   that opens it in the right editor.
  echo.
  echo   On a bankset cartridge the artwork is in the half the CPU never
  echo   reads. Drop it here and pass --side maria, or use the tools directly.
  goto :finish
)

set "IN=%~1"
set "EXT=%~x1"

if /i "!EXT!"==".a78" goto :open
if /i "!EXT!"==".bin" goto :open
if /i "!EXT!"==".rom" goto :open
echo   Not a cartridge. Drop a .a78, .bin or .rom.
echo   For a song or a capture, use "Open in tracker.bat" instead.
goto :finish

:open
echo Starting the workbench. Leave this window open while you work.
echo Closing it stops the workbench and any editors it opened.
echo.
%PY% "!WB!" "!IN!" --port !A7800_WORKBENCH_PORT!
if errorlevel 2 (
  echo.
  echo The workbench could not lay this cartridge out. The reason is above.
  echo.
  echo Four images in a 2,664-image library cannot be laid out: two SOUPER
  echo and two 512K flat. If yours is not one of those, the header may
  echo understate the mapping. To see what it claims:
  echo.
  echo   %PY% "%HERE%tools\cart.py" "!IN!"
  echo.
  echo cart.py takes --mapper and --low to override a header that is wrong,
  echo and the workbench takes --side for a bankset cartridge.
)

:finish
echo.
pause
endlocal
