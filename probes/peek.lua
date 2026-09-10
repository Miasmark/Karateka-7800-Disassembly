-- peek.lua -- read a fixed set of addresses at chosen frames and print them.
--
-- Env: A7800_PEEK_FRAMES = "600,1170" (comma-separated)
--      A7800_PEEK_ADDRS  = "186D,188C,2537,2575" (comma-separated hex, no $)

local MACHINE = (type(manager.machine) == "function")
                and manager:machine() or manager.machine
local mem = MACHINE.devices[":maincpu"].spaces["program"]

local FRAMES = {}
for tok in string.gmatch(os.getenv("A7800_PEEK_FRAMES") or "", "[^,]+") do
  FRAMES[tonumber(tok)] = true
end
local ADDRS = {}
for tok in string.gmatch(os.getenv("A7800_PEEK_ADDRS") or "", "[^,]+") do
  ADDRS[#ADDRS+1] = tonumber(tok, 16)
end

local F = 0
emu.register_frame_done(function()
  F = F + 1
  if FRAMES[F] then
    local parts = {}
    for _, a in ipairs(ADDRS) do
      parts[#parts+1] = string.format("$%04X=%d", a, mem:read_u8(a))
    end
    print(string.format("frame %d: %s", F, table.concat(parts, " ")))
  end
end)
