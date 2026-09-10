-- cadence.lua -- how far does one knockback move the world, and does the
-- 3-3-2 cycle actually cycle? Logs a near-world display-list head and the
-- two cycle bytes whenever either moves.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local OUT=os.getenv("A7800_CADENCE_LOG") or "cadence.log"
local f=io.open(OUT,"w")
f:write("frame near far goonx phaseA phaseB stage\n")
local F,last=0,""
emu.register_frame_done(function()
  F=F+1
  local s=string.format("%02X %02X %02X %02X %02X %02X",
    mem:read_u8(0x2423), mem:read_u8(0x2379), mem:read_u8(0x188C),
    mem:read_u8(0xF7), mem:read_u8(0xF8), mem:read_u8(0x18AA))
  if s~=last then f:write(tostring(F).." "..s.."\n"); last=s end
end)
if emu.add_machine_stop_notifier then
  CADENCE_STOPPER=emu.add_machine_stop_notifier(function() f:flush(); f:close() end)
end
