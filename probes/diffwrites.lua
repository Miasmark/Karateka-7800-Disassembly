-- diffwrites.lua -- which RAM addresses get written during a frame window.
--
-- Built to find the camera/scroll variable by elimination: run once over a
-- window where the screen is known to be panning, once over a window where
-- it is known to be stalled, and diff the two address sets -- whatever
-- writes during panning and not during the stall is a candidate.
--
-- Env: A7800_DW_LOG (default diffwrites.log), A7800_DW_FROM/TO (frame
-- window), A7800_DW_LO/HI (address range, default $1800-$20FF).

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local OUT  = os.getenv("A7800_DW_LOG") or "diffwrites.log"
local FROM = tonumber(os.getenv("A7800_DW_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_DW_TO") or "1000000000")
local LO   = tonumber(os.getenv("A7800_DW_LO") or "0x1800")
local HI   = tonumber(os.getenv("A7800_DW_HI") or "0x20FF")

local seen = {}
local F = 0

TAPS = {}
TAPS[1] = mem:install_write_tap(LO, HI, "range",
  function(offset, data)
    if F >= FROM and F <= TO then
      seen[offset] = (seen[offset] or 0) + 1
    end
    return data
  end)

emu.register_frame_done(function() F = F + 1 end)

local function dump()
  local f = io.open(OUT, "w")
  local addrs = {}
  for a in pairs(seen) do addrs[#addrs+1] = a end
  table.sort(addrs)
  for _, a in ipairs(addrs) do
    f:write(string.format("$%04X %d\n", a, seen[a]))
  end
  f:close()
  print("diffwrites: " .. #addrs .. " addresses written -> " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
