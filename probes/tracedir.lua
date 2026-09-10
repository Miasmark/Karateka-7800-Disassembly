-- tracedir.lua -- single-pass combined trace: fighter positions, $AA, and raw
-- input state, all from the SAME frame counter in the SAME run, to settle
-- the sign convention (does $AA's sign correlate with stick direction and
-- with which way the tracked coordinates move) without cross-log guessing.
--
-- Env: A7800_TD_FROM/TO, A7800_TD_LOG

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local FROM = tonumber(os.getenv("A7800_TD_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_TD_TO") or "1000000000")
local OUT  = os.getenv("A7800_TD_LOG") or "tracedir.log"

local f = io.open(OUT, "w")
f:write("frame playerX goonX AA AAsigned swcha a2 a3\n")

local F = 0
emu.register_frame_done(function()
  if F >= FROM and F <= TO then
    local px = mem:read_u8(0x186D)
    local gx = mem:read_u8(0x188C)
    local aa = mem:read_u8(0x00AA)
    local aas = aa
    if aas >= 128 then aas = aas - 256 end
    local swcha = mem:read_u8(0x0280)
    local a2 = mem:read_u8(0x00A2)
    local a3 = mem:read_u8(0x00A3)
    f:write(string.format("%d %d %d %d %d $%02X %d %d\n",
            F, px, gx, aa, aas, swcha, a2, a3))
  end
  F = F + 1
end)

local function dump()
  f:flush(); f:close()
  print("tracedir: wrote " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
