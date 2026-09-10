-- Frame markers for the a7800 fork, to go alongside a debugger watchpoint log.
--
-- The fork's Lua predates `install_write_tap`, so `probes/audio.lua` cannot see
-- anything there. Its debugger can: a watchpoint action may call `logerror`,
-- and `-log` sends that to error.log. What a watchpoint cannot tell you is when
-- one frame ends and the next begins, which is what a tracker row is.
--
-- So this runs beside it and stamps the frame number into the same log. The two
-- interleave in order, which is all the converter needs.
--
--   a7800 a7800 -cart game.a78 \
--         -debug -debugscript wp.txt \
--         -autoboot_script probes/a7800-frames.lua -autoboot_delay 1 \
--         -log -sound none -video none -nothrottle -str 40
--
-- `capture.py --emulator a7800` writes the watchpoint script and runs all this
-- for you; this file is the half that has to be a file.
--
-- A7800_AUDIO_FRAMES  how many frames to record before quitting (default 3600)
-- A7800_AUDIO_SKIP    frames to discard first
-- A7800_DRIVE         hold fire on a loop, for a game that waits at a title

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine

local STOP  = tonumber(os.getenv("A7800_AUDIO_FRAMES") or "3600")
local START = tonumber(os.getenv("A7800_AUDIO_SKIP") or "0")
local DRIVE = os.getenv("A7800_DRIVE")

local frame = 0
local fire = nil
if DRIVE then
  local ok, port = pcall(function()
    return MACHINE.ioport.ports[":buttons"].fields["P1 Button 1"]
  end)
  if ok then fire = port end
end

emu.register_frame_done(function()
  frame = frame + 1
  if fire then fire:set_value(((frame % 70) < 10) and 1 or 0) end
  if frame <= START then return end
  -- One marker a frame. The writes logged by the watchpoints since the last
  -- marker belong to the frame that just finished.
  MACHINE:logerror(string.format("A78FRAME %d\n", frame - START))
  if frame - START >= STOP then
    MACHINE:logerror(string.format("A78DONE %d\n", STOP))
    MACHINE:exit()
  end
end)
