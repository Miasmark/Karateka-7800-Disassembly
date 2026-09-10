-- dumpgfx.lua -- capture the display list and MARIA registers during a fight.
--
--   mame a7800 -cart karateka.a78 -autoboot_script probes/dumpgfx.lua \
--        -video none -sound none -nothrottle -skip_gameinfo
--
-- dumpdl.lua answers "what is MARIA drawing right now" for whatever frame you
-- pick. This is the same question asked at a frame that means something: it
-- presses Select to start the game, waits for $18AA (the encounter number) to
-- become non-zero -- so the title screen and the walk to the palace cannot be
-- mistaken for gameplay -- and only then taps and dumps.
--
-- It also captures every MARIA register, not just DPPH/DPPL: CHARBASE and
-- CTRL are what decide whether a display-list entry's graphics pointer is a
-- bitmap address or a character-list address, and what pixel format the bytes
-- at that address are in. Guessing either produces a confident wrong picture.
--
-- Writes:
--   dumpgfx_ram.bin    $1800-$27FF, the 7800's whole RAM
--   dumpgfx_regs.txt    every MARIA register write seen, and the frame it dumped on
--
-- Then:
--   python tools/dlwalk.py --raw dumpgfx_ram.bin --at 0x1800 --dll <DPPH DPPL> --follow
--   python tools/gfx.py <rom> --base <CHARBASE address> --ascending ...
--       (only if a zone turns out to be indirect; direct-mode zones point
--       straight at ROM or RAM bitmap data with dlwalk's own --gfx addr)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local ENCOUNTER = 0x18AA
local SETTLE = 900
local REACH  = tonumber(os.getenv("A7800_GFX_REACH") or "5400")
local DELAY  = tonumber(os.getenv("A7800_GFX_DELAY") or "60")   -- frames to
                                                                 -- wait once in
                                                                 -- a fight, so
                                                                 -- the dump is
                                                                 -- not the very
                                                                 -- first frame
local RAM_OUT  = os.getenv("A7800_GFX_RAM") or "dumpgfx_ram.bin"
local REG_OUT  = os.getenv("A7800_GFX_REGS") or "dumpgfx_regs.txt"

local cons = MACHINE.ioport.ports[":console_buttons"]

local regs = {}
TAPS = {}
TAPS[1] = mem:install_write_tap(0x20, 0x3F, "maria", function(offset, data)
  regs[offset] = data
  return data
end)

local frame, phase, reach_frame = 0, "boot", 0

local function dump()
  local f = io.open(RAM_OUT, "wb")
  for a = 0x1800, 0x27FF do f:write(string.char(mem:read_u8(a))) end
  f:close()

  local g = io.open(REG_OUT, "w")
  g:write(string.format("frame %d\n", frame))
  g:write(string.format("encounter %d\n", mem:read_u8(ENCOUNTER)))
  g:write(string.format("DPPH=$%02X DPPL=$%02X\n", regs[0x2C] or 0, regs[0x30] or 0))
  g:write(string.format("CHARBASE=$%02X OFFSET=$%02X CTRL=$%02X BACKGRND=$%02X\n",
    regs[0x34] or 0, regs[0x38] or 0, regs[0x3C] or 0, regs[0x20] or 0))
  g:write("palettes (P0C1-P7C3), 21-3F:\n")
  for a = 0x21, 0x3F do
    if a % 8 ~= 4 and a % 4 ~= 0 then   -- skip WSYNC(24)/MSTAT(28) slots roughly
      g:write(string.format("  $%02X = $%02X\n", a, regs[a] or 0))
    end
  end
  g:close()

  print(string.format(
    "dumpgfx: frame %d, encounter %d. DPPH=$%02X DPPL=$%02X CHARBASE=$%02X CTRL=$%02X",
    frame, mem:read_u8(ENCOUNTER), regs[0x2C] or 0, regs[0x30] or 0,
    regs[0x34] or 0, regs[0x3C] or 0))
  print("dumpgfx: wrote " .. RAM_OUT .. " and " .. REG_OUT)
  print(string.format(
    "dumpgfx: python tools/dlwalk.py --raw %s --at 0x1800 --dll 0x%02X%02X --follow",
    RAM_OUT, regs[0x2C] or 0, regs[0x30] or 0))
end

emu.register_frame_done(function()
  frame = frame + 1

  if phase == "boot" then
    cons.fields["Select"]:set_value((frame % 60 < 8) and 1 or 0)
    if frame > SETTLE and mem:read_u8(ENCOUNTER) ~= 0 then
      cons.fields["Select"]:set_value(0)
      phase, reach_frame = "wait", frame
    elseif frame > REACH then
      phase = "never"
    end
    return
  end

  if phase == "wait" then
    if frame - reach_frame >= DELAY then
      dump()
      MACHINE:exit()
    end
    return
  end

  if phase == "never" then
    print("dumpgfx: never reached an encounter in " .. REACH .. " frames; "
          .. "nothing captured. Try the start-at-four build or raise "
          .. "A7800_GFX_REACH.")
    MACHINE:exit()
  end
end)
