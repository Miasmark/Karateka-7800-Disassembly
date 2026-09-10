-- Log a running 7800 game's audio register writes, for tools/tracker.py.
--
--   TIA (every cartridge has one):
--     mame a7800 -cart game.a78 -autoboot_script probes/audio.lua \
--          -sound none -video none -str 40
--     python tools/tracker.py capture a7800-audio.log -o game.trk
--
--   POKEY (about a fifth of them; cart.py says which and where):
--     set A7800_POKEY to the base address first, e.g.
--     A7800_POKEY=0x0450 mame a7800 -cart game.a78 ...
--
-- Pass -sound none. The log is written from the emulation, not from the
-- speakers, so muting costs nothing -- and without it a -nothrottle run plays
-- the whole session at twenty times speed through your audio device.
--
-- The registers ARE the music: whatever the cartridge's player does to produce
-- them, the stream of writes is the same thing you would have to write
-- yourself. So this works on a game whose player has never been looked at.
--
-- One line per frame in which anything changed. TIA:
--     frame  c0 f0 v0  c1 f1 v1
-- POKEY:
--     frame  f1 c1  f2 c2  f3 c3  f4 c4  ctl
-- all hex. Frames where nothing changed are not logged; the tracker holds the
-- last value, which is what the chip does too.
--
-- KEEP THE TAPS IN A GLOBAL. A tap stored in a local is collected as soon as
-- the enclosing chunk finishes, and it stops firing without saying so -- which
-- looks exactly like a game that writes no audio.

local OUT   = os.getenv("A7800_AUDIO_LOG") or "a7800-audio.log"
local STOP  = tonumber(os.getenv("A7800_AUDIO_FRAMES") or "3600")
local START = tonumber(os.getenv("A7800_AUDIO_SKIP") or "0")
-- A7800_POKEY is one base, or two separated by a comma for the cartridges
-- that carry a second POKEY at $0440 beside the $0450. Those two chips are
-- written different values in most frames, so reading only the first throws
-- away half the music -- and MAME v0.287 does not emulate the second one at
-- all, which is why this taps the CPU bus rather than the chip.
local PBASE  = os.getenv("A7800_POKEY")          -- nil means TIA
local POKEYS = {}
if PBASE then
  for one in string.gmatch(PBASE, "[^,%s]+") do
    POKEYS[#POKEYS + 1] = tonumber(one)
  end
end
local POKEY = POKEYS[1]
-- Most cartridges are silent until someone presses a button, and this is a
-- passive logger. A7800_DRIVE=1 taps fire on a loop so a title screen gives
-- way and the music starts. It is a blunt instrument -- for anything that
-- needs real play, record a session and capture against -playback instead.
local DRIVE = os.getenv("A7800_DRIVE")

-- MAME and the a7800 fork expose the machine differently: newer MAME has
-- `manager.machine` as a property, a7800 v5.2 still has `manager:machine()`.
-- Getting this wrong fails at the first line, so resolve it once here.
local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem   = MACHINE.devices[":maincpu"].spaces["program"]
local nvals = POKEY and (9 * #POKEYS) or 6
local cur, last = {}, {}
for i = 1, nvals do cur[i] = 0; last[i] = -1 end

local frame = 0
local file  = io.open(OUT, "w")
AUDIO_TAPS = {}

if POKEY then
  local names, heads = {}, {}
  for i, base in ipairs(POKEYS) do
    names[#names + 1] = string.format("$%04X", base)
    local n = (i - 1) * 4
    heads[#heads + 1] = string.format("f%d c%d  f%d c%d  f%d c%d  f%d c%d  ctl",
      n + 1, n + 1, n + 2, n + 2, n + 3, n + 3, n + 4, n + 4)
  end
  file:write("# chip " .. (#POKEYS > 1 and "pokey2" or "pokey")
             .. "  base " .. table.concat(names, ",") .. "\n")
  file:write("# frame  " .. table.concat(heads, "   ") .. "   (hex)\n")
  for i, base in ipairs(POKEYS) do
    local slot0 = (i - 1) * 9              -- nine values per chip
    AUDIO_TAPS[#AUDIO_TAPS + 1] = mem:install_write_tap(base, base + 8,
      "pokeyaudio" .. i,
      function(offset, data)
        cur[slot0 + (offset - base) + 1] = data & 0xFF
        return data
      end)
  end
else
  file:write("# chip tia\n")
  file:write("# frame  c0 f0 v0  c1 f1 v1   (hex)\n")
  -- TIA is mirrored at $0100 and $0200; catch the writes wherever they land.
  local slot = {[0x15] = 1, [0x17] = 2, [0x19] = 3,
                [0x16] = 4, [0x18] = 5, [0x1A] = 6}
  for _, base in ipairs({0x00, 0x100, 0x200}) do
    AUDIO_TAPS[#AUDIO_TAPS + 1] = mem:install_write_tap(
      base + 0x15, base + 0x1A, "tiaaudio",
      function(offset, data)
        local s = slot[offset - base]
        if s then cur[s] = data & 0xFF end
        return data
      end)
  end
end

-- Find the button port by case-insensitive tag. MAME reports this one as
-- ":BUTTONS"; looking up ":buttons" returns nil, `fire` stays nil, and
-- A7800_DRIVE does NOTHING while still reporting that it is driving. That is
-- how every "driven" capture taken here for some time was actually a passive
-- one, which is not a difference you can see in the log it produces.
local buttons_port = nil
if DRIVE then
  for tag, port in pairs(MACHINE.ioport.ports) do
    if tag:lower() == ":buttons" then buttons_port = port end
  end
end
local fire = nil
if buttons_port then
  for name, field in pairs(buttons_port.fields) do
    if name:lower():find("p1") and name:lower():find("button 1") then fire = field end
  end
end
if DRIVE and not fire then
  print("A7800_DRIVE was asked for but no P1 fire button was found; "
        .. "this capture is PASSIVE. Do not compare it against a driven run.")
end

emu.register_frame_done(function()
  frame = frame + 1
  if fire then fire:set_value(((frame % 70) < 10) and 1 or 0) end
  if frame <= START then return end
  local changed = false
  for i = 1, nvals do
    if cur[i] ~= last[i] then changed = true end
  end
  if changed then
    local parts = {tostring(frame - START)}
    for i = 1, nvals do
      parts[#parts + 1] = string.format("%02X", cur[i])
      last[i] = cur[i]
    end
    file:write(table.concat(parts, " ") .. "\n")
  end
  if frame - START >= STOP then
    file:close()
    print(string.format("wrote %s (%d frames, %s)", OUT, STOP,
                        POKEY and "pokey" or "tia"))
    MACHINE:exit()
  end
end)
