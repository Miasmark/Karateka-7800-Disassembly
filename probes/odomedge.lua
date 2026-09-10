-- odomedge.lua -- force the hall's travel budget to a chosen state at a
-- frame, then watch what the next knockback does with it.
--   A7800_EDGE_FRAME  when to poke
--   A7800_EDGE_MODE   "wall"  = one unit left of the hall's left end
--                     "end0"  = park the odometer in segment 0
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local AT=tonumber(os.getenv("A7800_EDGE_FRAME") or "0")
local MODE=os.getenv("A7800_EDGE_MODE") or "wall"
local f=io.open(os.getenv("A7800_ODOM_LOG") or "odomedge.log","w")
f:write("frame ph b0 b1 b2 near playerbody cyc\n")
local F,last=0,""
emu.register_frame_done(function()
  F=F+1
  if F==AT then
    if MODE=="wall" then
      mem:write_u8(0x18B7,1); mem:write_u8(0x18B5,0); mem:write_u8(0x18B3,0)
      f:write("# poked: segment 2 has one unit left, 1 and 0 are empty\n")
    else
      mem:write_u8(0x18BD,0); mem:write_u8(0x18B3,50)
      f:write("# poked: parked in segment 0 with 50 units of budget\n")
    end
  end
  local s=string.format("%d %d %d %d %02X %02X %d",
    mem:read_u8(0x18BD), mem:read_u8(0x18B3), mem:read_u8(0x18B5),
    mem:read_u8(0x18B7), mem:read_u8(0x2423), mem:read_u8(0x2537),
    mem:read_u8(0xF7))
  if s~=last then f:write(tostring(F).." "..s.."\n"); last=s end
end)
if emu.add_machine_stop_notifier then
  EDGE_STOPPER=emu.add_machine_stop_notifier(function() f:flush(); f:close() end)
end
