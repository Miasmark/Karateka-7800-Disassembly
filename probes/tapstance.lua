-- tapstance.lua -- what fraction of brief taps does the game actually act on?
--
--   mame a7800 -cart karateka.a78 -autoboot_script probes/tapstance.lua \
--        -video none -sound none -nothrottle -skip_gameinfo
--
-- Prints a success rate over several hundred trials and writes `tapstance.log`.
--
-- ## Why a number and not an impression
--
-- "Tapping the stance button works about 90% of the time" is a real
-- observation and it cannot be compared with anything. The earlier figures in
-- this project -- 16% on the stock cartridge, 46% with the input latch -- came
-- from a fixed 33 ms tap counted by machine, and a hand-counted rate from
-- normal play is measuring a different thing with a different tap length. Both
-- can be true and neither tells you what the other would have said.
--
-- So this puts them on one scale: a tap of a fixed number of frames, repeated,
-- with an objective test of whether the game acted.
--
-- ## What counts as acting
--
-- $187C is the stance byte -- the command decoder branches on it to pick the
-- walking or fighting half. A stance change is that byte flipping, which is
-- not a matter of opinion. Press button 1 for TAP frames, then watch $187C for
-- WATCH frames; if it changed, the tap landed.
--
-- Trials are spaced far enough apart for the previous change to settle and for
-- the loop to be at an arbitrary point in its round -- otherwise the tap and
-- the round lock into step and the answer is either 100% or 0% depending on
-- the phase they happen to lock at, which is a very convincing way to measure
-- nothing.
--
-- ## Getting into a fight first
--
-- The stance byte only means anything once play has started, so the probe
-- presses Select until the game stops sitting in attract, waits for things to
-- settle, and only then begins. A run that reports zero trials never got in.
--
-- ## Knobs
--
--   A7800_TAP_FRAMES   how long the tap is, in frames   (default 2, ~33 ms)
--   A7800_TAP_WATCH    how long to wait for a response  (default 40)
--   A7800_TAP_GAP      frames between trials            (default 97, a prime)
--   A7800_TAP_TRIALS   how many                         (default 300)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local TAP    = tonumber(os.getenv("A7800_TAP_FRAMES") or "2")
local WATCH  = tonumber(os.getenv("A7800_TAP_WATCH") or "40")
local GAP    = tonumber(os.getenv("A7800_TAP_GAP") or "97")
local TRIALS = tonumber(os.getenv("A7800_TAP_TRIALS") or "300")
local OUT    = os.getenv("A7800_TAP_LOG") or "tapstance.log"

local STANCE = 0x187C
local ENCOUNTER = 0x18AA           -- 0 until an encounter is set up, then 1-6
local SETTLE = 900                 -- frames of pressing Select before starting
local REACH = tonumber(os.getenv("A7800_TAP_REACH") or "5400")

local cons = MACHINE.ioport.ports[":console_buttons"]
local btn  = MACHINE.ioport.ports[":buttons"]

local frame, trials, hits = 0, 0, 0
local reported = false
local control_ok = false
local CONTROL_HOLD = 120
local reached = 0
local phase, t0, before = "boot", 0, 0
local lag_sum, lag_n = 0, 0

-- MAME's "P1 Button 1" is INPT1 ($09); "P1 Button 2" is INPT0 ($08). The
-- stance change reads INPT0, so the field to drive is Button 2. Getting this
-- backwards produces a probe that presses nothing the game is looking at, and
-- reports the rate at which the stance happened to change on its own -- which
-- is a number, differs between builds, and means nothing at all.
local FIELD = os.getenv("A7800_TAP_FIELD") or "P1 Button 2"

-- Which control changes stance depends on the mapping under test.
--
--   stock   button 1 toggles it, from either stance
--   remap   button 1 goes walking -> fighting; *down* goes back
--
-- That difference is the point of having a mode at all. A toggle pressed for
-- long enough can flip twice and land back where it started, which reads as a
-- miss and may be exactly what the stock cartridge's impossible-looking 0% at
-- long presses was. A directional change cannot do that: holding "up" can only
-- ever mean go to fighting.
--
-- So the probe reads $187C first and presses whatever moves the stance *from
-- the stance it is actually in*. Pressing button 1 in fighting stance under
-- the remap is a punch, not a miss, and counting it as one would slander the
-- mapping.
local SCHEME = os.getenv("A7800_TAP_SCHEME") or "stock"
local joy = MACHINE.ioport.ports[":joysticks"]

local function press(on)
  if not on then
    btn.fields[FIELD]:set_value(0)
    joy.fields["P1 Down"]:set_value(0)
    return
  end
  if SCHEME == "remap" and mem:read_u8(STANCE) ~= 0 then
    joy.fields["P1 Down"]:set_value(1)      -- fighting -> walking
  else
    btn.fields[FIELD]:set_value(1)          -- walking -> fighting, or stock
  end
end

emu.register_frame_done(function()
  frame = frame + 1

  if phase == "control" then
    -- Hold the button down for a long time. If that does not move the stance,
    -- nothing this probe reports afterwards is about the button.
    press(true)
    if mem:read_u8(STANCE) ~= before then
      control_ok = true
      press(false)
      phase, t0 = "idle", frame
    elseif frame - t0 >= CONTROL_HOLD then
      press(false)
      phase, t0 = "idle", frame
    end
    return
  end

  if phase == "boot" then
    -- Select starts a 7800 game; stop touching the console afterwards, or the
    -- restarts look exactly like the game ignoring you.
    cons.fields["Select"]:set_value((frame % 60 < 8) and 1 or 0)
    -- Waiting a fixed number of frames after Select measures whatever the
    -- game happens to be doing then -- the title, the walk to the palace, an
    -- intermission. The stance byte means nothing outside a fight, so wait
    -- for one: $18AA is 0 until an encounter is set up and 1-6 afterwards.
    if frame > SETTLE and mem:read_u8(ENCOUNTER) ~= 0 then
      cons.fields["Select"]:set_value(0)
      reached = frame
      before = mem:read_u8(STANCE)
      phase, t0 = "control", frame
    elseif frame > REACH then
      phase = "never"
    end
    return
  end

  if phase == "idle" then
    if frame - t0 >= GAP then
      if trials >= TRIALS then
        phase = "done"
        return
      end
      before = mem:read_u8(STANCE)
      press(true)
      phase, t0 = "tapping", frame
    end
    return
  end

  if phase == "tapping" then
    if frame - t0 >= TAP then
      press(false)
      phase = "watching"
    end
    return
  end

  if phase == "watching" then
    if mem:read_u8(STANCE) ~= before then
      hits = hits + 1
      lag_sum = lag_sum + (frame - t0)
      lag_n = lag_n + 1
      trials = trials + 1
      phase, t0 = "idle", frame
    elseif frame - t0 >= WATCH then
      trials = trials + 1
      phase, t0 = "idle", frame
    end
    return
  end
end)

local function dump()
  if reported then return end
  reported = true
  local f = io.open(OUT, "w")
  if f then
    f:write("# tapstance\n")
    f:write(string.format("# tap_frames %d watch %d gap %d\n", TAP, WATCH, GAP))
    f:write(string.format("# frames %d\n", frame))
    f:write(string.format("trials %d\n", trials))
    f:write(string.format("hits %d\n", hits))
    if lag_n > 0 then
      f:write(string.format("mean_response_frames %.1f\n", lag_sum / lag_n))
    end
    f:close()
  end
  if phase == "never" or reached == 0 then
    print("tapstance: never reached an encounter in " .. REACH .. " frames, so "
          .. "no trial ran in a fight and there is nothing here. Give it "
          .. "longer with A7800_TAP_REACH, or use the start-at-four build.")
    return
  end
  if not control_ok then
    print("tapstance: THE CONTROL FAILED. Holding the button down for "
          .. CONTROL_HOLD .. " frames never moved the stance byte, so the "
          .. "button is not reaching the game and every rate below is just "
          .. "how often the stance changed by itself. Check the field name: "
          .. "MAME's P1 Button 1 is INPT1, P1 Button 2 is INPT0.")
    return
  end
  if trials == 0 then
    print("tapstance: no trials ran -- the game never left attract, so the "
          .. "stance byte never meant anything. Nothing here is a result.")
    return
  end
  print(string.format(
    "tapstance: %d/%d taps of %d frame(s) landed -- %.1f%%",
    hits, trials, TAP, 100.0 * hits / trials))
  if lag_n > 0 then
    print(string.format(
      "tapstance: when one landed it took %.1f frames to show",
      lag_sum / lag_n))
  end
  print("tapstance: compare builds at the same tap length; a rate without one "
        .. "is not comparable to anything.")
end

-- Held in a global on purpose: a notifier whose return value is dropped gets
-- collected, and the run then looks fine and writes nothing.
if emu.add_machine_stop_notifier then
  TAP_STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end

emu.register_frame_done(function()
  if phase == "never" then
    dump()
    MACHINE:exit()
  end
  if phase == "done" then
    dump()
    MACHINE:exit()
  end
end)
