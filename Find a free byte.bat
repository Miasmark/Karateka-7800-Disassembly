@echo off
setlocal enabledelayedexpansion
title Karateka free RAM
rem ---------------------------------------------------------------------------
rem  Which candidate zero-page bytes does Karateka never write?
rem
rem  Double-click, play a stage properly -- walk, fight, take a hit, die, see
rem  the next screen -- then close MAME.
rem
rem  ## Why this needs asking
rem
rem  A patch that has to remember something between calls needs a byte nobody
rem  else uses, and in this image reading the ROM cannot settle it. The Forth
rem  data stack lives in zero page indexed by X, X is based at $CF and grows
rem  down, and zero-page indexing wraps -- so an instruction that names $2A
rem  touches $F9 when the stack happens to be empty.
rem
rem  Of the 48 bytes above the stack base, four are never named directly
rem  anywhere in the ROM: $D7, $DC, $EF and $F9. All four are reachable in
rem  principle. This asks the machine which are reachable in practice.
rem
rem  $E8 is watched too, as a control: it is the interpreter's thread pointer
rem  and is written on every dispatch. If it reports zero writes the taps are
rem  broken and the rest of the run means nothing.
rem
rem  Nothing is modified.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "PROBE=%HERE%probes\freeram.lua"
set "LOG=%HERE%freeram.log"
set "BIOS=%HERE%..\bios"

if not exist "%PROBE%" (
  echo Could not find probes\freeram.lua next to this file.
  goto :finish
)

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

set "MAME=%LOCALAPPDATA%\Programs\MAME\mame.exe"
if not exist "!MAME!" set "MAME=C:\Program Files\MAME\mame.exe"
if not exist "!MAME!" set "MAME=%HERE%..\a7800-win-v5.2\a7800.exe"
if not exist "!MAME!" (
  echo Could not find MAME. Install it, or edit MAME in this batch file.
  goto :finish
)

echo   cartridge: !CART!
echo   probe    : probes\freeram.lua
echo.
echo   MAME is about to open. Get into a FIGHT and stay in one for a minute --
echo   the walk to the palace and the title screen tell us nothing here.
echo   Close MAME when you are done.
echo.
pause

if exist "!LOG!" del "!LOG!"
set "A7800_FREERAM_LOG=!LOG!"

"!MAME!" a7800 -rompath "%BIOS%" -cart "!CART!" -autoboot_script "%PROBE%" ^
    -window -skip_gameinfo

echo.
if not exist "!LOG!" (
  echo No log was written. If MAME reported a Lua error above, the probe does
  echo not match this build.
  goto :finish
)
echo Written to: !LOG!
echo.
type "!LOG!"
echo.
echo   A slot with 1 distinct value and 0 changes did nothing all fight. Count
echo   those: that is how many of the nine frames a round are being spent on
echo   nothing, and how much responsiveness is available for free.

:finish
echo.
pause
endlocal
