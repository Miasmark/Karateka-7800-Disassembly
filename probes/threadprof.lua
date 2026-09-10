-- threadprof.lua -- profile a threaded-code game while a person plays it.
--
--   mame a7800 -cart karateka.a78 -autoboot_script probes/threadprof.lua
--
-- Play normally for a minute, get into a fight, then close MAME. It writes
-- `threadprof.log` next to wherever MAME was started, and
-- `tools/forth.py --profile threadprof.log` says which definitions the game
-- actually spends itself in.
--
-- ## Why a person has to do the playing
--
-- Scripted input gets a game as far as its title screen and no further.
-- Everything measured about Karateka this way agreed on one thing: 93% of the
-- interpreter's work, in the states a script can reach, is an empty
-- `DO ... LOOP` -- the game counting to 512 to pass time. That may or may not
-- be what it does during a fight, and the difference decides whether the game
-- can be made faster by editing a few numbers or only by rewriting it.
--
-- A script cannot answer that. Someone holding the stick can.
--
-- ## How it works
--
-- The interpreter's inner loop stores its advanced thread pointer every
-- dispatch -- low byte then high. Tapping those two writes reconstructs the
-- pointer without touching the emulated machine, so the game runs exactly as
-- it would otherwise. Counts are bucketed by 16 bytes and written at exit.
--
-- Set A7800_IP to the zero-page pair if the game is not Karateka; find it with
-- `tools/forth.py <rom> --map`, which prints "the thread pointer lives at $xx".

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local IP    = tonumber(os.getenv("A7800_IP") or "0xE8")
local OUT   = os.getenv("A7800_PROF_LOG") or "threadprof.log"
local BUCKET = 16
-- Ignore the first SKIP frames entirely -- not counted in dispatches, loop
-- gaps or control reads. For asking "is this shape a property of ordinary
-- play, or of whatever the recording's opening few seconds happen to be
-- doing" without re-recording anything: replay the same .inp twice, once
-- plain and once with a SKIP past the suspect portion, and compare.
local SKIP = tonumber(os.getenv("A7800_PROF_SKIP") or "0") or 0

TAPS = {}                 -- global: a tap held in a local is collected
local hi, total, frames = 0, 0, 0
local buckets = {}

-- How often the game looks at the stick, from the same run. $0280 is SWCHA on
-- every 7800, so this needs no per-game knowledge, and it is the number that
-- decides what the controls feel like.
local PORT = tonumber(os.getenv("A7800_PORT") or "0x0280")
local reads, lastread, gaps = 0, nil, 0

-- How often the game's main loop comes round -- its think rate, and the thing
-- that actually decides responsiveness. Averaging control reads over a whole
-- session does not measure this: a menu polls the stick twice a frame, so a
-- session with more menu in it reports a *faster* cadence than one spent
-- fighting. A7800_LOOP is one thread address inside the loop, visited once per
-- iteration; the gaps between visits are the loop's period in frames.
local LOOP = tonumber(os.getenv("A7800_LOOP") or "0") or 0
local loops, lastloop, loopgaps, loopmin, loopmax = 0, nil, 0, 9999, 0
-- A histogram, not just a mean. Every average taken in this investigation has
-- been wrecked by a session containing more than one kind of state: a menu
-- polls fast, an intermission does not run the loop at all, and the mean lands
-- between two behaviours and describes neither. The distribution says what the
-- loop does when it is running.
local loophist = {}

TAPS[#TAPS + 1] = mem:install_read_tap(PORT, PORT, "controls",
  function(offset, data)
    if frames >= SKIP then
      reads = reads + 1
      if lastread then gaps = gaps + (frames - lastread) end
      lastread = frames
    end
    return data
  end)

TAPS[#TAPS + 1] = mem:install_write_tap(IP + 1, IP + 1, "ip-high",
  function(offset, data) hi = data; return data end)

TAPS[#TAPS + 1] = mem:install_write_tap(IP, IP, "ip-low",
  function(offset, data)
    local ip = (hi << 8) | data
    if frames >= SKIP then
      if LOOP ~= 0 and ip == LOOP then
        loops = loops + 1
        if lastloop then
          local g = frames - lastloop
          loopgaps = loopgaps + g
          if g < loopmin then loopmin = g end
          if g > loopmax then loopmax = g end
          local k = g
          if k > 64 then k = 65 end      -- everything long in one bucket
          loophist[k] = (loophist[k] or 0) + 1
        end
        lastloop = frames
      end
      local b = ip // BUCKET
      buckets[b] = (buckets[b] or 0) + 1
      total = total + 1
    end
    return data
  end)

emu.register_frame_done(function()
  frames = frames + 1
end)

local function dump()
  local f = io.open(OUT, "w")
  if not f then
    print("threadprof: could not write " .. OUT)
    return
  end
  local counted = frames - SKIP
  if counted < 0 then counted = 0 end
  f:write(string.format("# threaded-code profile\n"))
  f:write(string.format("# frames %d\n", counted))
  if SKIP > 0 then
    f:write(string.format("# skipped %d\n", SKIP))
    f:write(string.format("# sessionframes %d\n", frames))
  end
  f:write(string.format("# dispatches %d\n", total))
  f:write(string.format("# bucket %d\n", BUCKET))
  f:write(string.format("# inputreads %d\n", reads))
  if reads > 1 then
    f:write(string.format("# inputgap %.2f\n", gaps / (reads - 1)))
  end
  if loops > 1 then
    f:write(string.format("# loops %d\n", loops))
    f:write(string.format("# loopgap %.2f\n", loopgaps / (loops - 1)))
    f:write(string.format("# loopmin %d\n", loopmin))
    f:write(string.format("# loopmax %d\n", loopmax))
    for k = 0, 65 do
      if loophist[k] then
        f:write(string.format("# loophist %d %d\n", k, loophist[k]))
      end
    end
  end
  local keys = {}
  for k in pairs(buckets) do keys[#keys + 1] = k end
  table.sort(keys)
  for _, k in ipairs(keys) do
    f:write(string.format("%04X %d\n", k * BUCKET, buckets[k]))
  end
  f:close()
  print(string.format("threadprof: %d dispatches over %d frames%s -> %s",
                      total, counted,
                      SKIP > 0 and string.format(" (skipped the first %d of %d)",
                                                 SKIP, frames) or "",
                      OUT))
end

-- MAME renamed this; take whichever the build has so the log is always
-- written, including when the window is simply closed.
--
-- KEEP THE SUBSCRIPTION IN A GLOBAL, for the same reason as the taps above:
-- add_machine_stop_notifier hands back an object, and if it is dropped the
-- notifier is collected and never fires. The symptom is a run that looks
-- perfectly normal and writes no profile at all.
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
