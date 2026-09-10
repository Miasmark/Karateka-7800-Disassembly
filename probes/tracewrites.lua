-- tracewrites.lua -- who writes $186D (player X) and $00AA (walk velocity),
-- and what the threaded-code interpreter's IP was at that moment.
--
-- Built to chase a specific puzzle: in a captured session, $AA repeatedly
-- takes +8 while player X ($186D) never moves, then later a single $AA=-1
-- is immediately followed by X decreasing by 1. Something is suppressing
-- the positive writes specifically. Tapping the store site (not polling)
-- shows whether $186D's add-in never happens, or happens and is then
-- overwritten in the same frame.
--
--   -autoboot_script probes/tracewrites.lua  (same invocation as threadprof)
-- Env: A7800_TW_LOG (default tracewrites.log), A7800_TW_FROM/TO (frame window,
-- default 0..1e9 -- keep this narrow, the log gets big fast).

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local OUT  = os.getenv("A7800_TW_LOG") or "tracewrites.log"
local FROM = tonumber(os.getenv("A7800_TW_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_TW_TO") or "1000000000")

local f = io.open(OUT, "w")
f:write("frame addr old new ip pc\n")

local IP = tonumber(os.getenv("A7800_IP") or "0xE8")
local hi = 0
local ip = 0

local F = 0
TAPS = {}

TAPS[#TAPS+1] = mem:install_write_tap(IP + 1, IP + 1, "ip-high",
  function(offset, data) hi = data; return data end)
TAPS[#TAPS+1] = mem:install_write_tap(IP, IP, "ip-low",
  function(offset, data) ip = (hi << 8) | data; return data end)

local cpu = MACHINE.devices[":maincpu"]

local function watch(addr, name)
  TAPS[#TAPS+1] = mem:install_write_tap(addr, addr, name,
    function(offset, data)
      if F >= FROM and F <= TO then
        local old = mem:read_u8(addr)
        local pc = cpu.state["PC"].value
        f:write(string.format("%d $%04X %d %d $%04X $%04X\n", F, addr, old, data, ip, pc))
      end
      return data
    end)
end

watch(0x186D, "playerX")
watch(0x188C, "goonX")
watch(0x00AA, "AA")

emu.register_frame_done(function() F = F + 1 end)

local function dump()
  f:flush(); f:close()
  print("tracewrites: wrote " .. OUT .. " up to frame " .. F)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
