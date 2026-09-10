-- framecounter.lua -- show the running frame number on screen while a
-- recording plays back, so a frame seen on screen can be handed back for
-- precise tracing (screenshots, RAM dumps) at that exact frame.
--
-- Refreshes every frame so the on-screen message never fades out.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine

local F = 0
emu.register_frame_done(function()
  F = F + 1
  MACHINE:popmessage(string.format("frame %d", F))
end)
