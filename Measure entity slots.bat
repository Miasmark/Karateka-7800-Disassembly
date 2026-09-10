@echo off
setlocal enabledelayedexpansion
title Karateka entity slots
rem ---------------------------------------------------------------------------
rem  How many of Karateka's nine entities are actually doing anything.
rem
rem  Double-click, get into a one-on-one fight, fight for a minute, close MAME.
rem
rem  ## Why this is worth an hour
rem
rem  The main loop gives each of nine entity slots an update and then waits a
rem  whole frame, nine times round. That is the thirteen-frame cadence, and
rem  skipping waits fixes it while speeding the whole game up in exactly equal
rem  measure -- an entity's animation advances once per update, so responsiveness
rem  and speed are the same knob.
rem
rem  There is one way out of that trade and it rests on a fact nobody has
rem  measured: are all nine slots busy? If six sit on a do-nothing word while
rem  two people fight, the loop spends six frames a round on nothing, and
rem  skipping those particular waits would cost no speed at all.
rem
rem  If seven slots are busy, that idea is dead and the three days it would take
rem  are better spent elsewhere. Either answer is worth having, and this is the
rem  cheap way to get it.
rem
rem  Nothing is modified. The probe reads nine RAM addresses once a frame.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
set "PROBE=%HERE%probes\entityslots.lua"
set "LOG=%HERE%entityslots.log"
set "BIOS=%HERE%..\bios"

if not exist "%PROBE%" (
  echo Could not find probes\entityslots.lua next to this file.
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
echo   probe    : probes\entityslots.lua
echo.
echo   MAME is about to open. Get into a FIGHT and stay in one for a minute --
echo   the walk to the palace and the title screen tell us nothing here.
echo   Close MAME when you are done.
echo.
pause

if exist "!LOG!" del "!LOG!"
set "A7800_SLOT_LOG=!LOG!"

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
