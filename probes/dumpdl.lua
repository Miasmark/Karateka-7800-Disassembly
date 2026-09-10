-- dumpdl.lua -- find the display list list and dump RAM so it can be decoded.
--
--   mame a7800 -cart game.a78 -autoboot_script dumpdl.lua -video none -sound none -nothrottle -str 30
--   python tools/dlwalk.py --raw ramdump.bin --at 0x1800 --dll <the address printed> --follow
--
-- DPPH ($2C) and DPPL ($30) tell MARIA where the display list list lives, and
-- both are write-only: you cannot read back where the screen is being drawn
-- from. The only way to learn it is to watch the write go past, which is what
-- the tap below is for. (Keep the tap in a global -- an unreferenced tap is
-- garbage-collected and stops firing without saying so.)
--
-- Expect the printed address to be in the $0100-$01FF range as often as not.
-- That is a mirror of $2100-$21FF; dlwalk.py folds it for you.

-- MAME and the a7800 fork expose the machine differently: newer MAME has
-- `manager.machine` as a property, a7800 v5.2 still has `manager:machine()`.
-- Getting this wrong fails at the first line, so resolve it once here.
local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local DUMP_AT   = 1400              -- frame to snapshot
local DUMP_FROM = 0x1800            -- the 7800's RAM block
local DUMP_TO   = 0x27FF
local OUT       = "ramdump.bin"

local F, dpph, dppl = 0, nil, nil

TAP = mem:install_write_tap(0x20, 0x3F, "maria regs", function(offset, data)
  if offset == 0x2C then dpph = data end
  if offset == 0x30 then dppl = data end
  return data
end)

local buttons = MACHINE.ioport.ports[":buttons"]
local fire = buttons and buttons.fields["P1 Button 1"] or nil

emu.register_frame_done(function()
  F = F + 1
  if fire then
    fire:set_value((F > 300 and (F % 40) < 8) and 1 or 0)
  end
  if F == DUMP_AT then
    if dpph == nil then
      print("no DPPH write seen -- the game may set it once before frame 1,")
      print("or the tap was collected. Try dumping earlier.")
    else
      print(string.format("DLL = $%02X%02X", dpph, dppl or 0))
    end
    local f = io.open(OUT, "wb")
    for a = DUMP_FROM, DUMP_TO do f:write(string.char(mem:read_u8(a))) end
    f:close()
    print(string.format("wrote %s ($%04X-$%04X, %d bytes)",
                        OUT, DUMP_FROM, DUMP_TO, DUMP_TO - DUMP_FROM + 1))
    print("decode it with:")
    print(string.format("  python tools/dlwalk.py --raw %s --at 0x%04X --dll 0x%02X%02X --follow",
                        OUT, DUMP_FROM, dpph or 0, dppl or 0))
    MACHINE:exit()
  end
end)
