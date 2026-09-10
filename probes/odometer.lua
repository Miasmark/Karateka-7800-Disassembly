-- odometer.lua -- does a knockback spend the hall's travel budget?
-- Logs the five segment pairs, the phase index and the near-world head
-- whenever any of them moves, plus the knockback cycle byte.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local f=io.open(os.getenv("A7800_ODOM_LOG") or "odometer.log","w")
f:write("frame ph b0 a0 b1 a1 b2 a2 b3 a3 b4 a4 sum near cyc\n")
local F,last=0,""
emu.register_frame_done(function()
  F=F+1
  local v,sum={},0
  for i=0,9 do v[i+1]=mem:read_u8(0x18B3+i); sum=sum+v[i+1] end
  local s=string.format("%d %d %d %d %d %d %d %d %d %d %d %d %02X %d",
    mem:read_u8(0x18BD), v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],
    sum, mem:read_u8(0x2423), mem:read_u8(0xF7))
  if s~=last then f:write(tostring(F).." "..s.."\n"); last=s end
end)
if emu.add_machine_stop_notifier then
  ODOM_STOPPER=emu.add_machine_stop_notifier(function() f:flush(); f:close() end)
end
