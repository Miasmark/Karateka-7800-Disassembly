local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local F=0
local f=io.open(os.getenv("A7800_SL_LOG") or "slots.log","w")
f:write("frame slot6_18C5 slot4_1897 slot2_1878 18DC 18C0\n")
emu.register_frame_done(function()
  F=F+1
  if F>=8340 and F<=8460 then
    local function w(a) return mem:read_u8(a)|(mem:read_u8(a+1)<<8) end
    f:write(string.format("%d %04X %04X %04X %d %d\n",F,w(0x18C5),w(0x1897),w(0x1878),
            mem:read_u8(0x18DC),mem:read_u8(0x18C0)))
  end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
