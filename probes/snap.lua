-- snap.lua -- save a PNG screenshot at chosen frames, so a camera/positioning
-- question can be settled by looking at the screen instead of inferring it
-- from RAM addresses.
--
-- Screenshots land in MAME's own -snapshot_directory (pass it explicitly),
-- named by MAME's own convention, not this script's.
--
-- Env: A7800_SNAP_FRAMES = "100,200,300" (comma-separated frame numbers)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine

local FRAMES = {}
for tok in string.gmatch(os.getenv("A7800_SNAP_FRAMES") or "", "[^,]+") do
  FRAMES[tonumber(tok)] = true
end

local F = 0
emu.register_frame_done(function()
  F = F + 1
  if FRAMES[F] then
    local ok, err = pcall(function() MACHINE.video:snapshot() end)
    print(string.format("frame %d: snapshot ok=%s err=%s", F, tostring(ok), tostring(err)))
  end
end)
