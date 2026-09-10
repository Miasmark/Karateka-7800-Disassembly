@echo off
setlocal enabledelayedexpansion
title Atari 8-bit playtest
rem ---------------------------------------------------------------------------
rem  Play an Atari 8-bit disk in Altirra.
rem
rem  Double-click for the 8-bit Karateka, or drag any .atx/.atr/.xfd onto this.
rem
rem  Altirra is portable -- it lives in ..\altirra and installs nothing. It
rem  carries its own OS kernel (ATOS), so no Atari ROMs are needed, and it reads
rem  ATX natively, which matters here: Karateka's disk is copy protected with
rem  duplicate sectors, and a plain ATR loses exactly the thing the protection
rem  is made of.
rem
rem  Defaults to a 48K Atari 800, which is what the game was written for in
rem  1984. Set A8_HARDWARE and A8_MEM to override, e.g.
rem      set A8_HARDWARE=800xl
rem      set A8_MEM=64K
rem
rem  ## Recording
rem
rem  Altirra cannot record and replay *input* the way MAME can -- there is no
rem  equivalent of the .inp files used to measure the 7800 patches, so a session
rem  here cannot be replayed deterministically for before-and-after numbers.
rem
rem  What it does have, from the File menu once it is running:
rem      Record Video     an AVI of the session, for showing someone
rem      Save State       a snapshot to resume from, useful for starting a
rem                       measurement at the same point every time
rem
rem  For profiling, Altirra's own Debugger (F8) has a performance analyser,
rem  which is a better instrument than anything hand-built.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
rem No trailing backslash where it is passed quoted: "...\" escapes the quote.
set "DIR=%HERE:~0,-1%"
set "ALT=%HERE%..\altirra\Altirra64.exe"
if not exist "%ALT%" set "ALT=%HERE%..\altirra\Altirra.exe"
if not exist "%ALT%" (
  echo Could not find Altirra in ..\altirra
  echo Get it from https://www.virtualdub.org/altirra.html and unzip it there.
  goto :finish
)

set "DISK=%~1"
if not "%DISK%"=="" goto :gotdisk
set "DISK=%HERE%..\karateka\karateka.atx"
if exist "!DISK!" goto :gotdisk
echo Nothing dropped on this file, and no karateka.atx to fall back on.
echo Drag an .atx, .atr or .xfd onto this batch file.
goto :finish
:gotdisk
if not exist "!DISK!" (
  echo Not there: !DISK!
  goto :finish
)

if not defined A8_HARDWARE set "A8_HARDWARE=800"
if not defined A8_MEM set "A8_MEM=48K"

echo   disk    : !DISK!
echo   machine : Atari !A8_HARDWARE!, !A8_MEM!, NTSC, BASIC disabled
echo.
echo   Altirra is opening. It records video from File ^> Record Video,
echo   and its debugger is F8. It cannot record input for replay.
echo.

"%ALT%" /hardware:!A8_HARDWARE! /memsize:!A8_MEM! /ntsc /nobasic "!DISK!"

:finish
echo.
pause
endlocal
