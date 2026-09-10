-- entityslots.lua -- how many of Karateka's nine entities are doing anything.
--
--   mame a7800 -cart karateka.a78 -autoboot_script probes/entityslots.lua
--
-- Play until you are in a one-on-one fight, fight for a minute, then close
-- MAME. It writes `entityslots.log` next to wherever MAME was started.
--
-- ## The question this exists to answer
--
-- Karateka's main loop gives each of nine entity slots an update and then
-- waits a whole frame, nine times round:
--
--     LIT $18AD  @  EXECUTE     ( run whatever word this slot points at )
--     wait one frame
--     LIT $1878  @  EXECUTE
--     wait one frame
--     ...
--
-- Thirteen frames a decision, and that is the pacing everybody complains
-- about. Skipping waits fixes it and speeds the whole game up in exactly equal
-- measure, because an entity's animation advances once per update.
--
-- There is one way out of that trade, and it depends entirely on a fact nobody
-- has measured: **are all nine slots actually busy?** If six of them sit on a
-- do-nothing word while two people fight, the loop is spending six frames a
-- round on nothing, and skipping *those* waits would cost no speed at all.
--
-- If seven slots are busy, that idea is worthless and the three days it would
-- take are better spent elsewhere. An hour of playing settles it.
--
-- ## How it works
--
-- Each slot holds the address of the behaviour word that entity is currently
-- running. Sampling the nine addresses once a frame costs nothing and needs no
-- taps: a slot whose pointer never changes across a whole fight is either idle
-- or stuck in a one-word loop, and either way its frame is being wasted.
--
-- What comes out per slot: how many distinct behaviours it held, how often it
-- changed, and the single value it spent most of its time on.
--
-- **Read the dwell, not the idle count.** The first run of this reported "0 of
-- 9 idle" and that summary was useless: nothing in a running game holds one
-- pointer and never changes it, so a test for exactly that can only ever come
-- back zero. The signal was in the dwell column, where four slots turned out
-- to be sitting on a single word for 96% of the fight while two changed nine
-- hundred times. The binary was the wrong question asked of the right data.
--
-- ## The positive control, and why it is not optional
--
-- The whole output of this probe is a count of things that did *not* happen,
-- and a tap reporting zero is the easiest measurement in the world to get
-- wrong: a wrong address, a wrong moment, a filter that excludes the thing
-- being asked about, and the result is a confident, clean, false negative that
-- nobody revisits because a ruled-out cause is not re-checked.
--
-- So one slot in this run is already known to be live. $1878 is the player's:
-- w_7898 turns each command into a behaviour word and writes it there. If the
-- player's slot reports no changes across a fight, the instrument is broken
-- and the other eight numbers mean nothing. The probe says so itself rather
-- than leaving it to be noticed.
--
-- This costs nothing -- the slot was being sampled anyway -- and converts the
-- worst failure this probe can have from silent into obvious.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local OUT = os.getenv("A7800_SLOT_LOG") or "entityslots.log"

-- The nine slots, in the order the main loop w_A59C visits them. The order
-- matters: the wait after slot n is the one that would be skipped if slot n
-- turns out to be idle, so the report has to name which is which.
local SLOTS = {0x18AD, 0x1878, 0x18AF, 0x1897, 0x18B1,
               0x18C5, 0x18C7, 0x18DF, 0x18C9}

-- $187C says which stance the player is in, and it is the cheapest signal that
-- a fight is happening rather than a menu or the walk to the palace. Slots are
-- counted separately for the two, because "how many entities are busy" has a
-- different answer on the title screen and nobody is asking about that one.
local STANCE = 0x187C

-- The player's slot, sampled as a control: it cannot be idle during a fight.
local CONTROL = 0x1878

local frames, fighting = 0, 0
local seen, changes, last, dwell = {}, {}, {}, {}
for i = 1, #SLOTS do
  seen[i], changes[i], dwell[i] = {}, 0, {}
end

local function sample(only_fighting)
  for i, addr in ipairs(SLOTS) do
    local v = mem:read_u16(addr)
    if only_fighting then
      seen[i][v] = (seen[i][v] or 0) + 1
      dwell[i][v] = (dwell[i][v] or 0) + 1
      if last[i] and last[i] ~= v then changes[i] = changes[i] + 1 end
      last[i] = v
    end
  end
end

emu.register_frame_done(function()
  frames = frames + 1
  local in_fight = mem:read_u8(STANCE) ~= 0
  if in_fight then fighting = fighting + 1 end
  sample(in_fight)
end)

local function dump()
  local f = io.open(OUT, "w")
  if not f then
    print("entityslots: could not write " .. OUT)
    return
  end
  f:write("# entityslots\n")
  f:write(string.format("# frames %d\n", frames))
  f:write(string.format("# fighting %d\n", fighting))
  f:write("# slot address distinct changes commonest dwell\n")
  local idle = 0
  for i, addr in ipairs(SLOTS) do
    local n, best, bestn = 0, 0, 0
    for v, c in pairs(seen[i]) do
      n = n + 1
      if c > bestn then best, bestn = v, c end
    end
    if n <= 1 and changes[i] == 0 then idle = idle + 1 end
    f:write(string.format("slot %d %04X %d %d %04X %d\n",
                          i, addr, n, changes[i], best, bestn))
  end
  f:write(string.format("# idle %d of %d\n", idle, #SLOTS))

  -- The control, written into the log so it travels with the result rather
  -- than living only in whoever remembers to look.
  local ctrl_i
  for i, addr in ipairs(SLOTS) do
    if addr == CONTROL then ctrl_i = i end
  end
  local ctrl_changes = ctrl_i and changes[ctrl_i] or -1
  f:write(string.format("# control %04X changes %d\n",
                        CONTROL, ctrl_changes))
  f:close()

  -- Lead with the distribution. "Never changed" is a summary that cannot
  -- distinguish a slot doing nothing from one doing the same thing all fight,
  -- and it is the second that actually happens.
  local quiet = 0
  for i = 1, #SLOTS do
    local best = 0
    for _, c in pairs(seen[i]) do
      if c > best then best = c end
    end
    if fighting > 0 and best > fighting * 0.9 then quiet = quiet + 1 end
  end
  print(string.format(
    "entityslots: %d frames, %d fighting. %d of 9 slots held one word for over "
    .. "90%% of it, %d never changed at all -> %s",
    frames, fighting, quiet, idle, OUT))
  if fighting < 300 then
    print("entityslots: under five seconds of fighting was sampled, which is "
          .. "not enough to conclude anything. Get into a fight and stay in "
          .. "one.")
  elseif ctrl_changes <= 0 then
    print("entityslots: THE CONTROL FAILED. Slot $1878 is the player's and it "
          .. "did not change once, which cannot be true across a fight. The "
          .. "probe is reading the wrong thing, or at the wrong time -- every "
          .. "other number in this run means nothing.")
  else
    print(string.format(
      "entityslots: control ok -- the player's slot changed %d times, so the "
      .. "instrument was working when the other eight reported what they did.",
      ctrl_changes))
  end
end

-- Held in a global on purpose: the notifier is collected if its return value
-- is dropped, and the symptom is a run that looks fine and writes nothing.
if emu.add_machine_stop_notifier then
  SLOT_STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
