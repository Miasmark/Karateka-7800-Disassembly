-- armsnap.lua -- force the stance snapshot $E7 to 1 at a frame, then snapshot a window.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local ARM=tonumber(os.getenv("A7800_ARM_FRAME") or "0")
local FROM=tonumber(os.getenv("A7800_SNAP_FROM") or "0")
local TO=tonumber(os.getenv("A7800_SNAP_TO") or "0")
local STEP=tonumber(os.getenv("A7800_SNAP_STEP") or "1")
local F=0
emu.register_frame_done(function()
  F=F+1
  if F>=ARM and F<=ARM+6 then mem:write_u8(0xE7,1) end
  if F>=FROM and F<=TO and ((F-FROM)%STEP)==0 then
    pcall(function() M.video:snapshot() end)
    print(string.format("snap %d  E7=%02X 1878=%04X",F,mem:read_u8(0xE7),
      mem:read_u8(0x1878)|(mem:read_u8(0x1879)<<8)))
  end
  if F>TO then M:exit() end
end)
