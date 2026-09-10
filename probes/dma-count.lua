local M = (type(manager.machine)=="function") and manager:machine() or manager.machine
local mem = M.devices[":maincpu"].spaces["program"]
local F, samples = 0, {}
emu.register_frame_done(function()
  F = F + 1
  if F > 120 and F <= 200 then
    samples[#samples+1] = mem:read_u8(0x90) + mem:read_u8(0x91)*256
  end
  if F > 200 then
    table.sort(samples)
    print(string.format("RESULT median=%d min=%d max=%d n=%d",
          samples[math.floor(#samples/2)], samples[1], samples[#samples], #samples))
    M:exit()
  end
end)
