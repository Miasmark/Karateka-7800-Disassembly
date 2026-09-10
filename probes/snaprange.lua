-- snaprange.lua -- screenshot every Nth frame in a window, then stop.
-- Env: A7800_SNAP_FROM, A7800_SNAP_TO, A7800_SNAP_STEP
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local FROM=tonumber(os.getenv("A7800_SNAP_FROM") or "0")
local TO=tonumber(os.getenv("A7800_SNAP_TO") or "0")
local STEP=tonumber(os.getenv("A7800_SNAP_STEP") or "1")
local F=0
emu.register_frame_done(function()
  F=F+1
  if F>=FROM and F<=TO and ((F-FROM)%STEP)==0 then
    pcall(function() M.video:snapshot() end)
    print(string.format("snap frame %d", F))
  end
  if F>TO then M:exit() end
end)
