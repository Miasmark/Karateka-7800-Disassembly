@echo off
setlocal enabledelayedexpansion
title 7800 session recorder
rem ---------------------------------------------------------------------------
rem  Record a play session so it can be replayed and measured.
rem
rem  Double-click to record Karateka, or drag any .a78 onto this file.
rem
rem  MAME opens as normal. Play, GET INTO A FIGHT, then close MAME. The
rem  recording lands beside this batch file as <cartridge>.inp.
rem
rem  A replay reproduces the session exactly -- same frames, same inputs, same
rem  everything -- which is what makes it measurable. Two replays of the same
rem  recording produce byte-identical profiles, so a before-and-after number
rem  finally means something.
rem
rem  ## Record one per cartridge
rem
rem  A recording is a list of button states against frame numbers, not a list
rem  of intentions. Replay it on a build that runs at a different speed and the
rem  same presses arrive at different moments in the game -- the fight will go
rem  somewhere else entirely. So to compare the original against a patch,
rem  record a session on each rather than reusing one.
rem
rem  Aim for a couple of minutes with real fighting in it. Menus and walking
rem  measure the wrong thing: scripted input can reach those, and everything
rem  measured that way was misleading.
rem ---------------------------------------------------------------------------

set "HERE=%~dp0"
rem %~dp0 ends with a backslash, and "...\" escapes the closing quote on a
rem Windows command line, silently mangling every argument after it. That is
rem why an earlier version of this recorded nothing: MAME never saw -record.
rem DIR is the same path without the trailing backslash, for quoted use.
set "DIR=%HERE%..\karateka\recordings"
set "BIOS=%HERE%..\bios"

set "CART=%~1"
if not "%CART%"=="" goto :gotcart
set "CART=%HERE%..\Rom Library\Trebors 7800 ROM PROPack v8_17\Retail_v7_0\NTSC\Karateka (NTSC) (Atari) (1987) (FEC21472).a78"
if exist "!CART!" goto :gotcart
echo Could not find Karateka, and nothing was dropped on this file.
echo Drag a .a78 onto this batch file.
goto :finish
:gotcart
if not exist "!CART!" (
  echo Not there: !CART!
  goto :finish
)

for %%F in ("!CART!") do set "NAME=%%~nF"
set "INP=!NAME!.inp"

set "MAME=%LOCALAPPDATA%\Programs\MAME\mame.exe"
if not exist "!MAME!" set "MAME=C:\Program Files\MAME\mame.exe"
if not exist "!MAME!" (
  echo Could not find MAME. Edit MAME in this batch file.
  goto :finish
)

echo   cartridge: !CART!
echo   recording to: !INP!
echo.
echo   Play normally and get into a fight. Two minutes is plenty.
echo   Close MAME when you are done.
echo.
pause

"!MAME!" a7800 -rompath "%BIOS%" -cart "!CART!" ^
    -input_directory "%DIR%" -record "!INP!" -window -skip_gameinfo

echo.
if exist "%HERE%!INP!" (
  for %%F in ("%HERE%!INP!") do echo   Saved: !INP!  ^(%%~zF bytes^)
  echo.
  echo   It can now be replayed and measured, as many times as needed,
  echo   with identical results each time.
) else (
  echo   No recording was written. If MAME reported an error above, that is why.
)

:finish
echo.
pause
endlocal
