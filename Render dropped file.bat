@echo off
setlocal enabledelayedexpansion
title 7800 tracker -- render
rem ---------------------------------------------------------------------------
rem  Drag a file onto this and it renders to a WAV beside it, then plays it.
rem
rem  Takes any of three kinds of file, and does the right thing with each:
rem     .a78   a cartridge               -> capture it in MAME, then render
rem     .log   a MAME audio capture      -> convert to .trk first, then render
rem     .trk   a tracker song            -> render straight to WAV
rem
rem  For a cartridge it reads the header itself to find out whether the sound
rem  is TIA or a POKEY and where, and picks a7800 or a7800p from the region --
rem  nothing to choose. MAME and the 7800 BIOS have to be findable: set
rem  A7800_MAME and A7800_ROMPATH if they are not in the usual places.
rem
rem  Several files can be dropped at once; each is handled in turn. The chip
rem  (TIA or POKEY) comes from the file itself, so nothing needs choosing.
rem
rem  Nothing is overwritten silently: an existing .wav or .trk is moved aside
rem  with a .bak suffix first.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "TRACKER=%HERE%tools\tracker.py"
set "CAPTURE=%HERE%tools\capture.py"

rem  How many seconds of the cartridge to record. Long enough for a title tune
rem  to get going, short enough not to sit here.
if not defined A7800_SECONDS set "A7800_SECONDS=40"

if not exist "%TRACKER%" (
  echo Cannot find tools\tracker.py next to this batch file.
  echo Expected: %TRACKER%
  echo.
  echo Keep this file in the a7800-toolkit folder, beside tools\.
  goto :finish
)

rem -- find a Python. The py launcher is the reliable one on Windows; the
rem    App Execution Alias called "python" opens the Store if none is installed.
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
  echo   7800 tracker -- render
  echo.
  echo   Drag a file onto this batch file:
  echo.
  echo      game.a78            a cartridge -- captured in MAME, then rendered
  echo      a7800-audio.log     a MAME capture, converted then rendered
  echo      song.trk            a tracker song, rendered to song.wav
  echo.
  echo   A cartridge needs MAME and the 7800 BIOS. If they are not found,
  echo   set them first:
  echo      set A7800_MAME=C:\path\to\mame.exe
  echo      set A7800_ROMPATH=C:\path\to\bios
  echo   and set A7800_SECONDS to record more or less than 40 seconds.
  goto :finish
)

set "FAILED=0"

:next
if "%~1"=="" goto :summary
call :handle "%~1"
shift
goto :next

rem ---------------------------------------------------------------------------
rem  Everything below expands with !name! rather than %name%. 7800 filenames
rem  are full of parentheses -- "Food Fight (NTSC) (Atari) (1987).a78" -- and
rem  %name% is substituted while the IF block is being parsed, so those
rem  brackets close the block early and cmd complains that "(Atari) was
rem  unexpected at this time". !name! is substituted when the line runs, by
rem  which point the block is already understood.
:handle
set "IN=%~1"
set "EXT=%~x1"
set "STEM=%~dpn1"
set "SHORT=%~nx1"
echo.
echo ==^> !SHORT!

if not exist "!IN!" (
  echo     no such file
  set /a FAILED+=1
  goto :eof
)

if /i "!EXT!"==".a78" goto :fromcart
if /i "!EXT!"==".log" goto :fromlog
if /i "!EXT!"==".trk" goto :dorender
echo     not a .a78 cartridge, .log capture or .trk song -- skipped
set /a FAILED+=1
goto :eof

rem -- a cartridge: MAME captures it, and capture.py writes the .trk
:fromcart
call :backup "!STEM!.trk"
call :backup "!STEM!.log"
echo     capturing !A7800_SECONDS!s in MAME...
%PY% "!CAPTURE!" "!IN!" --seconds !A7800_SECONDS!
if errorlevel 1 (
  echo     capture failed -- see the message above
  set /a FAILED+=1
  goto :eof
)
set "IN=!STEM!.trk"
goto :dorender

rem -- a capture log becomes a song first
:fromlog
call :backup "!STEM!.trk"
echo     converting the capture...
%PY% "!TRACKER!" capture "!IN!" -o "!STEM!.trk"
if errorlevel 1 (
  echo     could not read that log
  set /a FAILED+=1
  goto :eof
)
set "IN=!STEM!.trk"

:dorender
call :backup "!STEM!.wav"
echo     rendering...
%PY% "!TRACKER!" render "!IN!" -o "!STEM!.wav"
if errorlevel 1 (
  echo     not rendered -- see the message above
  set /a FAILED+=1
  goto :eof
)

if exist "!STEM!.wav" (
  for %%W in ("!STEM!.wav") do echo     playing %%~nxW
  start "" "!STEM!.wav"
)
goto :eof

rem -- move an existing file aside rather than clobbering it
:backup
if not exist "%~1" goto :eof
if exist "%~1.bak" del "%~1.bak"
move /y "%~1" "%~1.bak" >nul
echo     kept the previous %~nx1 as %~nx1.bak
goto :eof

rem ---------------------------------------------------------------------------
:summary
echo.
if "%FAILED%"=="0" (
  echo Done.
) else (
  echo Done, with %FAILED% file^(s^) not rendered.
)

:finish
echo.
pause
endlocal
