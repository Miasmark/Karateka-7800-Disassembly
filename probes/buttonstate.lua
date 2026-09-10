-- buttonstate.lua -- log the two fire-button lines (INPT0/INPT1, the 7800's
-- analog-style button inputs, bit7 = not pressed) alongside a slot write, to
-- check whether a button is held together with the direction that arms the
-- world-mover chain.
--
-- Env: A7800_BS_FROM/TO, A7800_BS_LOG

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local FROM = tonumber(os.getenv("A7800_BS_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_BS_TO") or "1000000000")
local OUT  = os.getenv("A7800_BS_LOG") or "buttonstate.log"

local f = io.open(OUT, "w")
f:write("frame slotval inpt0 inpt1 swcha a2\n")

local F = 0
TAPS = {}
local function watch(addr)
  TAPS[#TAPS+1] = mem:install_write_tap(addr, addr, "slot",
    function(offset, data)
      if F >= FROM and F <= TO then
        local lo = mem:read_u8(0x1878)
        local hi = mem:read_u8(0x1879)
        local inpt0 = mem:read_u8(0x0008)
        local inpt1 = mem:read_u8(0x0009)
        local swcha = mem:read_u8(0x0280)
        local a2 = mem:read_u8(0x00A2)
        f:write(string.format("%d $%04X $%02X $%02X $%02X %d\n",
                F, (hi<<8)|lo, inpt0, inpt1, swcha, a2))
      end
      return data
    end)
end
watch(0x1878)

emu.register_frame_done(function() F = F + 1 end)

local function dump()
  f:flush(); f:close()
  print("buttonstate: wrote " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
