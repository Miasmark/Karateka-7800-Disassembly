-- freeram.lua -- which candidate bytes does Karateka never write?
--
--   mame a7800 -cart karateka.a78 -autoboot_script probes/freeram.lua
--
-- Play a full stage, fight, die, get to the next screen, then close MAME. It
-- writes `freeram.log` next to wherever MAME was started.
--
-- ## Why this is needed at all
--
-- A patch that has to remember something between calls needs a byte nobody
-- else uses, and in this image that cannot be settled by reading:
--
--   * The Forth data stack lives in zero page, indexed by X. X is based at
--     $CF (`LDX #$CF` at $408F) and grows down, so `LDA $55,X` names one
--     address in the listing and touches a different one on every call.
--   * Zero-page indexing wraps. A base of $2A with an empty stack reaches
--     $F9; a base of $08 with a shallow one reaches $D7. Which of those
--     actually happens depends on how deep the stack is at that instruction,
--     which is a runtime fact.
--   * Of $D0-$FF -- everything above the stack base -- exactly four bytes are
--     never named directly anywhere in the ROM: $D7, $DC, $EF and $F9. Every
--     one of them is reachable *in principle* from some indexed base at some
--     stack depth.
--
-- So the static answer is "no byte is provably free", which is true and
-- useless. This asks the machine instead.
--
-- ## The positive control
--
-- The whole output is a list of things that did not happen, and a tap
-- reporting zero is the easiest measurement to get wrong. $E8 is watched
-- alongside the candidates: it is the Forth interpreter's thread pointer and
-- it is written on every single dispatch. If the control reports no writes,
-- the tap is not working and none of the other zeroes mean anything.
--
-- ## Reading the result
--
-- A candidate with zero writes across a real session is a byte a patch can
-- have. It is evidence, not proof -- a code path nobody reached could still
-- use it -- so prefer one that also has no plausible indexed base near it, and
-- play widely rather than for a long time in one screen.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local OUT = os.getenv("A7800_FREERAM_LOG") or "freeram.log"

-- The four bytes in $D0-$FF that no instruction in the ROM names directly,
-- plus $E8 as the control.
local WATCH = {0xD7, 0xDC, 0xEF, 0xF9, 0xE8}
local CONTROL = 0xE8

local writes, firstpc, frames = {}, {}, 0
for _, a in ipairs(WATCH) do
  writes[a] = 0
end

TAPS = {}
for _, addr in ipairs(WATCH) do
  local a = addr
  TAPS[#TAPS + 1] = mem:install_write_tap(
    a, a, "freeram",
    function(offset, data, mask)
      writes[a] = writes[a] + 1
      if not firstpc[a] then
        -- where it came from, so a hit can be looked up in the disassembly
        firstpc[a] = MACHINE.devices[":maincpu"].state["PC"].value
      end
      return data
    end)
end

emu.register_frame_done(function()
  frames = frames + 1
end)

local function dump()
  local f = io.open(OUT, "w")
  if not f then
    print("freeram: could not write " .. OUT)
    return
  end
  f:write("# freeram\n")
  f:write(string.format("# frames %d\n", frames))
  f:write("# addr writes first_pc\n")
  for _, a in ipairs(WATCH) do
    f:write(string.format("%02X %d %04X\n", a, writes[a], firstpc[a] or 0))
  end
  f:close()

  if writes[CONTROL] == 0 then
    print("freeram: THE CONTROL FAILED. $E8 is the interpreter's thread "
          .. "pointer and is written every dispatch; zero writes means the "
          .. "taps are not working. Ignore the rest of this run.")
    return
  end
  local free = {}
  for _, a in ipairs(WATCH) do
    if a ~= CONTROL and writes[a] == 0 then
      free[#free + 1] = string.format("$%02X", a)
    end
  end
  print(string.format(
    "freeram: %d frames, control saw %d writes to $E8 so the taps worked.",
    frames, writes[CONTROL]))
  for _, a in ipairs(WATCH) do
    if a ~= CONTROL then
      print(string.format("   $%02X  %d write(s)%s", a, writes[a],
                          writes[a] > 0
                          and string.format(", first from PC $%04X",
                                            firstpc[a] or 0)
                          or ""))
    end
  end
  if #free == 0 then
    print("freeram: every candidate was written. A patch needing state will "
          .. "have to carry it somewhere else -- see the notes at the top.")
  else
    print("freeram: untouched this run -> " .. table.concat(free, " ")
          .. "  (evidence, not proof: play widely before relying on it)")
  end
end

-- Held in a global on purpose: the notifier is collected if its return value
-- is dropped, and the symptom is a run that looks fine and writes nothing.
if emu.add_machine_stop_notifier then
  FREERAM_STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
