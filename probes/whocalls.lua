-- whocalls.lua -- set an execution breakpoint at a given address and log
-- the 6502 return-address stack the first few times it's hit, to find a
-- caller that no static scan can (an indirect/computed jump).
--
-- Env: A7800_WC_ADDR (hex, no $), A7800_WC_LOG, A7800_WC_MAX (default 8)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]
local cpu = MACHINE.devices[":maincpu"]

local ADDR = tonumber(os.getenv("A7800_WC_ADDR") or "0", 16)
local OUT  = os.getenv("A7800_WC_LOG") or "whocalls.log"
local MAXHITS = tonumber(os.getenv("A7800_WC_MAX") or "8")

local f = io.open(OUT, "w")
f:write(string.format("watching $%04X\n", ADDR))

local F = 0
emu.register_frame_done(function() F = F + 1 end)

local hits = 0
local function on_exec(offset, data)
  hits = hits + 1
  local sreg = cpu.state["S"].value
  -- the 6502 stack lives at $0100-$01FF; S points just past the top
  local ra_lo = mem:read_u8(0x0100 + ((sreg + 1) & 0xFF))
  local ra_hi = mem:read_u8(0x0100 + ((sreg + 2) & 0xFF))
  local ra = (ra_hi << 8) | ra_lo
  f:write(string.format("frame %d  hit #%d  S=$%02X  return addr guess $%04X (JSR site ~ $%04X)\n",
          F, hits, sreg, ra, ra - 2))
  if hits >= MAXHITS then
    f:flush(); f:close()
    print("whocalls: done, wrote " .. OUT)
    MACHINE:exit()
  end
  return data
end

if cpu.debug and cpu.debug.bpset then
  cpu.debug:bpset(ADDR, nil, function() on_exec(ADDR, 0) end)
  print("whocalls: breakpoint set via debugger at $" .. string.format("%04X", ADDR))
else
  print("whocalls: no debugger interface available on this cpu device")
  f:write("no debugger interface available\n")
end

local function dump()
  f:flush(); f:close()
end
if emu.add_machine_stop_notifier then
  emu.add_machine_stop_notifier(dump)
end
