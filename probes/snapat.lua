-- snapat.lua -- run to an exact frame, take one screenshot, stop.
-- Env: A7800_SNAPAT_FRAME (required)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine

local TARGET = tonumber(os.getenv("A7800_SNAPAT_FRAME") or "0")

local F = 0
emu.register_frame_done(function()
  F = F + 1
  if F == TARGET then
    local ok, err = pcall(function() MACHINE.video:snapshot() end)
    print(string.format("frame %d: snapshot ok=%s err=%s", F, tostring(ok), tostring(err)))
    MACHINE:exit()
  end
end)
