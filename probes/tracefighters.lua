-- tracefighters.lua -- per-frame trace of the two fighters' state.
--
-- Polls (not taps -- simpler, and correct here since we want the settled
-- end-of-frame values, not every intermediate write) a fixed set of RAM
-- cells every frame and writes one line per frame to A7800_TRACE_LOG.
-- Built for one specific question: what do player X ($186D), goon X
-- ($188C), the shared walk-velocity byte ($AA), the w_9518 gate flag
-- ($18DC) and player stance ($187C) do around a reported stuck screen.
--
--   mame a7800 -cart game.a78 -autoboot_script probes/tracefighters.lua \
--        -input_directory DIR -playback session.inp -video none -sound none \
--        -nothrottle -seconds_to_run N

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local OUT = os.getenv("A7800_TRACE_LOG") or "tracefighters.log"
local f = io.open(OUT, "w")
f:write("frame playerX goonX AA AAsigned d18DC stance18C7C encounter18AA slot4 b4 b3 bd\n")

local function s8(v)
  if v >= 0x80 then return v - 0x100 else return v end
end

local F = 0
emu.register_frame_done(function()
  F = F + 1
  local px = mem:read_u8(0x186D)
  local gx = mem:read_u8(0x188C)
  local aa = mem:read_u8(0x00AA)
  local dc = mem:read_u8(0x18DC)
  local st = mem:read_u8(0x187C)
  local enc = mem:read_u8(0x18AA)
  local slot4 = mem:read_u8(0x1897) | (mem:read_u8(0x1898) << 8)
  local b4 = mem:read_u8(0x18B4)
  local b3 = mem:read_u8(0x18B3)
  local bd = mem:read_u8(0x18BD)
  f:write(string.format("%d %d %d %d %d %d %d %d %04X %d %d %d\n",
          F, px, gx, aa, s8(aa), dc, st, enc, slot4, b4, b3, bd))
end)

local function dump()
  f:flush()
  f:close()
  print("tracefighters: wrote " .. OUT .. " over " .. F .. " frames")
end

if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
