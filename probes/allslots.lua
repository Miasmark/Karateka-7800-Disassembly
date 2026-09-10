local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local S={{"1_18AD",0x18AD},{"2_1878",0x1878},{"3_18AF",0x18AF},{"4_1897",0x1897},
         {"5_18B1",0x18B1},{"6_18C5",0x18C5},{"7_18C7",0x18C7},{"8_18DF",0x18DF},
         {"9_18C9",0x18C9}}
local F=0
local f=io.open(os.getenv("A7800_AS_LOG") or "allslots.log","w")
f:write("frame"); for _,v in ipairs(S) do f:write(" "..v[1]) end; f:write("\n")
emu.register_frame_done(function()
  F=F+1
  if F>=8350 and F<=8400 then
    f:write(tostring(F))
    for _,v in ipairs(S) do
      f:write(string.format(" %04X", mem:read_u8(v[2])|(mem:read_u8(v[2]+1)<<8)))
    end
    f:write("\n")
  end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
