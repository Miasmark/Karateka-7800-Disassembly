-- inputlag.lua -- how often does a game actually look at its controls?
--
--   mame a7800 -cart game.a78 -autoboot_script probes/inputlag.lua \
--        -video none -sound none -nothrottle -skip_gameinfo
--
-- A game that reads the stick every frame can answer in one frame. A game that
-- reads it every fourteenth frame cannot, however tight the rest of its code
-- is -- and looking at the reading code will never show you that, because the
-- read itself is always cheap. What costs is how often the program gets round
-- to it. So this measures the interval, not the instruction.
--
-- Karateka, the game this was written for, samples SWCHA once every 13.4
-- frames during play: about four and a half times a second, on a machine
-- drawing sixty. Every kick and punch in that game is a joystick direction.
--
-- It also counts the threaded-code interpreter's dispatches, if the game has
-- one. Set A7800_LAG_NEXT to the address of the interpreter's inner loop --
-- for Karateka that is $00EA, where reset plants a JMP (indirect) opcode and
-- runs it out of RAM. Fetching an opcode is a memory read, so a read tap
-- there counts one hit per interpreted operation, which is a direct measure of
-- how much work the game gets done per frame.
--
-- ## Starting the game
--
-- Most cartridges sit in attract mode until a console switch is pressed, and
-- an attract screen polls nothing -- measure there and you will conclude the
-- game never reads its controls at all. This presses Select, which is what
-- starts a game on a 7800, until the first read of the port lands, then
-- *stops touching the console*. That second half matters: pressing Reset every couple
-- of hundred frames restarts the game, and the restarts show up in the
-- histogram as long gaps that look exactly like the game ignoring you.
--
-- KEEP THE TAPS IN A GLOBAL -- a tap held only in a local is collected and
-- silently stops firing, which reads as a game that never looks at anything.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local PORT   = tonumber(os.getenv("A7800_LAG_ADDR") or "0x0280")   -- SWCHA
local NEXTPC = tonumber(os.getenv("A7800_LAG_NEXT") or "0")        -- 0 = off
local STOP   = tonumber(os.getenv("A7800_LAG_FRAMES") or "4200")
local SETTLE = tonumber(os.getenv("A7800_LAG_SETTLE") or "600")

TAPS = {}                       -- global on purpose; see the note above
local frame, started, disp, total = 0, nil, 0, 0
-- `disp` is zeroed at each input sample to measure the loop; `total` never
-- is, because the per-frame figure needs a counter nothing else resets.
local samples, per_loop, per_frame = {}, {}, {}

TAPS[#TAPS + 1] = mem:install_read_tap(PORT, PORT, "controls",
  function(offset, data)
    if not started then
      started = frame
    else
      samples[#samples + 1] = frame
      if NEXTPC ~= 0 and frame > started + SETTLE then
        per_loop[#per_loop + 1] = disp
      end
    end
    disp = 0
    return data
  end)

if NEXTPC ~= 0 then
  TAPS[#TAPS + 1] = mem:install_read_tap(NEXTPC, NEXTPC, "next",
    function(offset, data)
      disp = disp + 1
      total = total + 1
      return data
    end)
end

local cons = MACHINE.ioport.ports[":console_buttons"]
local btn  = MACHINE.ioport.ports[":buttons"]
local joy  = MACHINE.ioport.ports[":joysticks"]
local framedisp = 0

local function stats(list)
  local t, lo, hi = 0, math.huge, 0
  for _, v in ipairs(list) do
    t = t + v
    if v < lo then lo = v end
    if v > hi then hi = v end
  end
  return t / #list, lo, hi
end

emu.register_frame_done(function()
  frame = frame + 1
  if NEXTPC ~= 0 then
    per_frame[frame] = total - framedisp
    framedisp = total
  end

  if not started then
    -- Select first, because that is what starts a game on this machine and
    -- what the manuals tell you to press. Karateka will not start on Reset or
    -- on the fire button at all -- tested, and both alone leave it in attract
    -- forever. Reset is offered later only as a fallback for a cartridge that
    -- wants it, and never once the game is running.
    cons.fields["Select"]:set_value((frame % 60 < 8) and 1 or 0)
    if frame > 900 then
      cons.fields["Reset"]:set_value((frame % 100 < 6) and 1 or 0)
      btn.fields["P1 Button 1"]:set_value((frame % 40 < 6) and 1 or 0)
    end
  else
    -- hands off the console from here, or the restarts pollute the histogram
    cons.fields["Reset"]:set_value(0)
    cons.fields["Select"]:set_value(0)
    btn.fields["P1 Button 1"]:set_value((frame % 90 < 10) and 1 or 0)
    joy.fields["P1 Right"]:set_value(1)
  end

  if frame == STOP then
    if not started then
      print("the game never read $" .. string.format("%04X", PORT) ..
            " -- it may not have left its attract screen, or it reads the " ..
            "controls somewhere else")
      MACHINE:exit()
      return
    end
    local lo = started + SETTLE
    local t = {}
    for _, f in ipairs(samples) do if f >= lo then t[#t + 1] = f end end
    print(string.format("polling began at frame %d", started))
    print(string.format("port $%04X sampled %d times over frames %d-%d",
                        PORT, #t, lo, STOP))
    if #t > 1 then
      local hist, tot = {}, 0
      for i = 2, #t do
        local g = t[i] - t[i - 1]
        hist[g] = (hist[g] or 0) + 1
        tot = tot + g
      end
      local mean = tot / (#t - 1)
      print(string.format("mean %.2f frames between samples = %.1f per second",
                          mean, 60 / mean))
      local ks = {}
      for k in pairs(hist) do ks[#ks + 1] = k end
      table.sort(ks)
      for _, k in ipairs(ks) do
        print(string.format("   %3d frames apart: %5d times", k, hist[k]))
      end
    end
    if NEXTPC ~= 0 and #per_loop > 0 then
      local m, a, b = stats(per_loop)
      print(string.format("interpreted operations between one look and the " ..
                          "next: mean %.0f  min %d  max %d", m, a, b))
      local f = {}
      for i = lo, STOP do f[#f + 1] = per_frame[i] or 0 end
      local fm, fa, fb = stats(f)
      print(string.format("interpreted operations per frame: mean %.1f  " ..
                          "min %d  max %d", fm, fa, fb))
    end
    MACHINE:exit()
  end
end)
