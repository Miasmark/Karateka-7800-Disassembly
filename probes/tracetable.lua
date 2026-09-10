-- tracetable.lua -- every RAM write in a frame window, tagged with the true
-- 6502 PC that performed it.
--
-- Built to find what address $65C8's indirect STA ($02,X) actually lands on
-- during a normal walk step -- a write-tap on a fixed address can't catch a
-- computed pointer, so this taps a whole range and filters by PC afterward.
--
-- Env: A7800_TT_LOG, A7800_TT_FROM/TO, A7800_TT_LO/HI (default full RAM).

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]
local cpu = MACHINE.devices[":maincpu"]

local OUT  = os.getenv("A7800_TT_LOG") or "tracetable.log"
local FROM = tonumber(os.getenv("A7800_TT_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_TT_TO") or "1000000000")
local LO   = tonumber(os.getenv("A7800_TT_LO") or "0x0000")
local HI   = tonumber(os.getenv("A7800_TT_HI") or "0x27FF")

local f = io.open(OUT, "w")
f:write("frame addr old new pc\n")

local F = 0
TAPS = {}
TAPS[1] = mem:install_write_tap(LO, HI, "range",
  function(offset, data)
    if F >= FROM and F <= TO then
      local old = mem:read_u8(offset)
      local pc = cpu.state["PC"].value
      f:write(string.format("%d $%04X %d %d $%04X\n", F, offset, old, data, pc))
    end
    return data
  end)

emu.register_frame_done(function() F = F + 1 end)

local function dump()
  f:flush(); f:close()
  print("tracetable: wrote " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
