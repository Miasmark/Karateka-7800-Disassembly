-- regcapture.lua -- capture every MARIA register at natural machine-stop,
-- in dumpgfx_regs.txt's own format, without pressing Select (which fights
-- a played-back recording's own input the way dumpgfx.lua's boot phase
-- does). Pair with -playback and ramdump.lua at the same -seconds_to_run
-- for a matched RAM + register capture.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local ENCOUNTER = 0x18AA
local REG_OUT = os.getenv("A7800_GFX_REGS") or "dumpgfx_regs.txt"

local regs = {}
TAPS = {}
TAPS[1] = mem:install_write_tap(0x20, 0x3F, "maria", function(offset, data)
  regs[offset] = data
  return data
end)

local F = 0
emu.register_frame_done(function() F = F + 1 end)

local function dump()
  local g = io.open(REG_OUT, "w")
  g:write(string.format("frame %d\n", F))
  g:write(string.format("encounter %d\n", mem:read_u8(ENCOUNTER)))
  g:write(string.format("DPPH=$%02X DPPL=$%02X\n", regs[0x2C] or 0, regs[0x30] or 0))
  g:write(string.format("CHARBASE=$%02X OFFSET=$%02X CTRL=$%02X BACKGRND=$%02X\n",
    regs[0x34] or 0, regs[0x38] or 0, regs[0x3C] or 0, regs[0x20] or 0))
  g:write("palettes (P0C1-P7C3), 21-3F:\n")
  for a = 0x21, 0x3F do
    if a % 8 ~= 4 and a % 4 ~= 0 then
      g:write(string.format("  $%02X = $%02X\n", a, regs[a] or 0))
    end
  end
  g:close()
  print(string.format(
    "regcapture: frame %d, encounter %d, DPPH=$%02X DPPL=$%02X -> %s",
    F, mem:read_u8(ENCOUNTER), regs[0x2C] or 0, regs[0x30] or 0, REG_OUT))
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
