local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local cpu = MACHINE.devices[":maincpu"]
for name, space in pairs(cpu.spaces) do
    print("space: " .. name)
end
MACHINE:exit()
