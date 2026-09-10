-- inputtrace.lua -- log the raw joystick port and decoded stick direction
-- every frame in a window, to settle whether a character's lack of movement
-- reflects no input or a broken response to input.
--
-- Env: A7800_IT_FROM/TO, A7800_IT_LOG

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local FROM = tonumber(os.getenv("A7800_IT_FROM") or "0")
local TO   = tonumber(os.getenv("A7800_IT_TO") or "1000000000")
local OUT  = os.getenv("A7800_IT_LOG") or "inputtrace.log"

local f = io.open(OUT, "w")
f:write("frame swcha a2 a3\n")

local F = 0
emu.register_frame_done(function()
  F = F + 1
  if F >= FROM and F <= TO then
    local swcha = mem:read_u8(0x0280)
    local a2 = mem:read_u8(0x00A2)
    local a3 = mem:read_u8(0x00A3)
    f:write(string.format("%d $%02X %d %d\n", F, swcha, a2, a3))
  end
end)

local function dump()
  f:flush(); f:close()
  print("inputtrace: wrote " .. OUT)
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
