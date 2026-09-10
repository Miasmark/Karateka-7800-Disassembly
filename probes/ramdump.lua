-- ramdump.lua -- dump a RAM range to a binary file at natural machine-stop.
-- Env: A7800_DUMP_LO, A7800_DUMP_HI (defaults 0x1800, 0x27FF), A7800_DUMP_OUT

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local LO = tonumber(os.getenv("A7800_DUMP_LO") or "0x1800")
local HI = tonumber(os.getenv("A7800_DUMP_HI") or "0x27FF")
local OUT = os.getenv("A7800_DUMP_OUT") or "ramdump.bin"

local F = 0
emu.register_frame_done(function() F = F + 1 end)

local function dump()
  local f = io.open(OUT, "wb")
  for a = LO, HI do
    f:write(string.char(mem:read_u8(a)))
  end
  f:close()
  print(string.format("stopped at frame %d: dumped $%04X-$%04X -> %s", F, LO, HI, OUT))
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
