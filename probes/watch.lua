-- watch.lua -- see what a running 7800 game actually does.
--
-- Run with:
--   mame a7800  -cart game.a78 -autoboot_script watch.lua -video none -sound none -nothrottle -str 30
--   mame a7800p -cart game.a78 ...        (a7800p is the PAL machine)
--
-- Everything below is configuration. Edit the three tables, run, read stdout.
--
-- ===========================================================================
-- THE ONE THING THAT WILL WASTE YOUR AFTERNOON
-- ===========================================================================
-- install_write_tap returns a tap object. If you do not keep a reference to it
-- in a variable that outlives the call, Lua garbage-collects it and the tap
-- SILENTLY STOPS FIRING -- typically after the first few frames, once the
-- collector first runs. You get output, it looks plausible, and it is a lie:
-- you will conclude "nothing ever writes here" about an address written sixty
-- times a second. Assign every tap to a global, as done below.
-- ===========================================================================

-- MAME and the a7800 fork expose the machine differently: newer MAME has
-- `manager.machine` as a property, a7800 v5.2 still has `manager:machine()`.
-- Getting this wrong fails at the first line, so resolve it once here.
local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

-- Addresses to watch for writes. Ranges are inclusive.
local WATCH = {
  { name = "MARIA palettes", lo = 0x20, hi = 0x3F },
  -- { name = "some RAM flag", lo = 0x1F35, hi = 0x1F35 },
}

-- Report a summary at these frames, then exit at the last one.
local REPORT_AT = { 600, 1200, 1800 }

-- Hold the fire button in bursts so a title screen advances into play.
local AUTOFIRE = { from = 300, to = 100000, period = 40, hold = 8 }

-- ---------------------------------------------------------------------------
local F = 0
local hits = {}          -- address -> count
local values = {}        -- address -> set of values seen
local first_frame = {}   -- address -> frame first written

TAPS = {}                -- global on purpose; see the warning above
for _, w in ipairs(WATCH) do
  TAPS[#TAPS + 1] = mem:install_write_tap(w.lo, w.hi, w.name,
    function(offset, data)
      hits[offset] = (hits[offset] or 0) + 1
      values[offset] = values[offset] or {}
      values[offset][data] = true
      if first_frame[offset] == nil then first_frame[offset] = F end
      return data
    end)
end

local fire = MACHINE.ioport.ports[":buttons"]
local fire_field = fire and fire.fields["P1 Button 1"] or nil

local function report(tag)
  local addrs = {}
  for a in pairs(hits) do addrs[#addrs + 1] = a end
  table.sort(addrs, function(x, y) return hits[x] > hits[y] end)
  print(string.format("--- %s (frame %d) ---", tag, F))
  if #addrs == 0 then
    print("    no writes seen. If that is surprising, check that the tap is")
    print("    held in a global -- an unreferenced tap stops firing quietly.")
  end
  for i = 1, math.min(#addrs, 24) do
    local a = addrs[i]
    local n = 0
    for _ in pairs(values[a]) do n = n + 1 end
    print(string.format("    $%04X  %6d writes  %3d distinct values  first at frame %d",
                        a, hits[a], n, first_frame[a]))
  end
end

emu.register_frame_done(function()
  F = F + 1
  if fire_field then
    local on = (F >= AUTOFIRE.from and F <= AUTOFIRE.to
                and (F % AUTOFIRE.period) < AUTOFIRE.hold)
    fire_field:set_value(on and 1 or 0)
  end
  for i, at in ipairs(REPORT_AT) do
    if F == at then
      report("report " .. i)
      if i == #REPORT_AT then MACHINE:exit() end
    end
  end
end)
