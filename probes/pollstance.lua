local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local F=0
local f=io.open(os.getenv("A7800_PS_LOG") or "pollstance.log","w")
f:write("frame stance187C snapE7 inpt0 inpt1\n")
emu.register_frame_done(function()
  F=F+1
  if F>=7300 and F<=8420 then
    f:write(string.format("%d %d %d %d %d\n",F,mem:read_u8(0x187C),
            mem:read_u8(0x00E7),mem:read_u8(0x08),mem:read_u8(0x09)))
  end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
