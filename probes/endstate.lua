local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local F=0
local f=io.open(os.getenv("A7800_ES_LOG") or "endstate.log","w")
f:write("frame 187C 187A 187B 18A7 08 09 A2 A3 E7 18AA 1878 1879\n")
-- sample just before each of the three stage-6 exits
local W={{8180,8420},{15940,16180},{22440,22700}}
emu.register_frame_done(function()
  F=F+1
  for _,w in ipairs(W) do
    if F>=w[1] and F<=w[2] then
      f:write(string.format("%d %d %d %d %d %d %d %d %d %d %d %d %d\n",F,
        mem:read_u8(0x187C),mem:read_u8(0x187A),mem:read_u8(0x187B),
        mem:read_u8(0x18A7),mem:read_u8(0x08),mem:read_u8(0x09),
        mem:read_u8(0xA2),mem:read_u8(0xA3),mem:read_u8(0xE7),
        mem:read_u8(0x18AA),mem:read_u8(0x1878),mem:read_u8(0x1879)))
    end
  end
end)
if emu.add_machine_stop_notifier then emu.add_machine_stop_notifier(function() f:flush(); f:close() end) end
