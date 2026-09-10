-- slotinput.lua -- log every write to the player's dispatch slot ($1878/79)
-- together with the raw input state at that exact frame, to find what
-- input state corresponds to installing a specific behaviour word.
--
-- Env: A7800_SI_FROM/TO, A7800_SI_LOG

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local FROM = tonumber(os.getenv("A7800_SI_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_SI_TO") or "1000000000")
local OUT  = os.getenv("A7800_SI_LOG") or "slotinput.log"

local f = io.open(OUT, "w")
f:write("frame slotlo slothi swcha a2 a3 08 09\n")

local F = 0
TAPS = {}
local function watch(addr)
  TAPS[#TAPS+1] = mem:install_write_tap(addr, addr, "slot",
    function(offset, data)
      if F >= FROM and F <= TO then
        local lo = mem:read_u8(0x1878)
        local hi = mem:read_u8(0x1879)
        local swcha = mem:read_u8(0x0280)
        local a2 = mem:read_u8(0x00A2)
        local a3 = mem:read_u8(0x00A3)
        local b08 = mem:read_u8(0x0008)
        local b09 = mem:read_u8(0x0009)
        f:write(string.format("%d $%02X $%02X $%02X %d %d %d %d\n",
                F, lo, hi, swcha, a2, a3, b08, b09))
      end
      return data
    end)
end
watch(0x1878)
watch(0x1879)

emu.register_frame_done(function() F = F + 1 end)

local function dump()
  f:flush(); f:close()
  print("slotinput: wrote " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
