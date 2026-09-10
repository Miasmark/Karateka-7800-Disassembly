local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local F=0
local f=io.open(os.getenv("A7800_AS_LOG") or "deathchain.log","w")
f:write("frame 2_1878 4_1897 18AA 18DC 18BF 18C1 187C E7\n")
local last=""
emu.register_frame_done(function()
  F=F+1
  if F>=8340 and F<=9200 then
    local s=string.format("%04X %04X %02X %02X %02X %02X %02X %02X",
      mem:read_u8(0x1878)|(mem:read_u8(0x1879)<<8),
      mem:read_u8(0x1897)|(mem:read_u8(0x1898)<<8),
      mem:read_u8(0x18AA), mem:read_u8(0x18DC), mem:read_u8(0x18BF),
      mem:read_u8(0x18C1), mem:read_u8(0x187C), mem:read_u8(0xE7))
    if s~=last then f:write(tostring(F).." "..s.."\n"); last=s end
  end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
