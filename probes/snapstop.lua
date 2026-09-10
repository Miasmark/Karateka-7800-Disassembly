-- snapstop.lua -- one screenshot, taken at natural machine-stop (not
-- mid-stream), so there is no risk of the snapshot call itself perturbing
-- MAME's frame accounting the way an in-stream call turned out to.
--
-- Run once per desired frame, with -seconds_to_run tuned to stop there.

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine

local F = 0
emu.register_frame_done(function() F = F + 1 end)

local function dump()
  local ok, err = pcall(function() MACHINE.video:snapshot() end)
  print(string.format("stopped at frame %d: snapshot ok=%s err=%s", F, tostring(ok), tostring(err)))
end
if emu.add_machine_stop_notifier then
  STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
