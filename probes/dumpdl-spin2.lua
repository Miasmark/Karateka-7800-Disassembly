local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]
local DUMP_AT = 6985
local F, dpph, dppl = 0, nil, nil
TAP = mem:install_write_tap(0x20, 0x3F, "maria regs", function(offset, data)
  if offset == 0x2C then dpph = data end
  if offset == 0x30 then dppl = data end
  return data
end)
emu.register_frame_done(function()
  F = F + 1
  if F == DUMP_AT then
    local f = io.open("ramdump-spin.bin", "wb")
    for a = 0x1800, 0x27FF do f:write(string.char(mem:read_u8(a))) end
    f:close()
    print(string.format("wrote ramdump-spin.bin, DLL=$%02X%02X", dpph or 0, dppl or 0))
    MACHINE:exit()
  end
end)
