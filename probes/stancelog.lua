local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local F=0
local f=io.open(os.getenv("A7800_AS_LOG") or "stancelog.log","w")
f:write("frame 187C E7 18AA 1878\n")
local last=""
emu.register_frame_done(function()
  F=F+1
  local s=string.format("%02X %02X %02X %04X",
    mem:read_u8(0x187C), mem:read_u8(0xE7), mem:read_u8(0x18AA),
    mem:read_u8(0x1878)|(mem:read_u8(0x1879)<<8))
  local k=s:sub(1,8)
  if k~=last then f:write(tostring(F).." "..s.."\n"); last=k end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
