-- palvisible.lua -- is the patched code actually mapped in?
-- On a bankswitched cartridge a patch can be present in the file and
-- invisible to the CPU, so this samples the CPU's view of the addresses
-- the fixes write to and counts how often each holds what it should.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local WATCH={
  {"BITZERO $B402", 0xB402, {0xB5,0x00,0x35,0x02}},   -- fix 48's word
  {"shove   $B300", 0xB300, {0x20,0x30,0x7A}},        -- fix 44's player hook
  {"cell    $A0EA", 0xA0EA, {0x00,0xB4}},             -- repointed at BITZERO
  {"mainloop$A59C", 0xA59C, nil},                     -- a landmark, for scale
}
local seen,total={},0
for i=1,#WATCH do seen[i]=0 end
local F=0
emu.register_frame_done(function()
  F=F+1
  if F<120 or F>1200 then return end
  total=total+1
  for i,w in ipairs(WATCH) do
    if w[3] then
      local ok=true
      for k,b in ipairs(w[3]) do
        if mem:read_u8(w[2]+k-1)~=b then ok=false break end
      end
      if ok then seen[i]=seen[i]+1 end
    end
  end
end)
local function dump()
  local f=io.open(os.getenv("A7800_PV_LOG") or "palvisible.log","w")
  f:write(string.format("# frames sampled %d\n",total))
  for i,w in ipairs(WATCH) do
    if w[3] then
      f:write(string.format("%-16s visible in %d of %d frames (%.1f%%)\n",
        w[1],seen[i],total,total>0 and 100*seen[i]/total or 0))
    end
  end
  f:close()
end
if emu.add_machine_stop_notifier then PV=emu.add_machine_stop_notifier(dump) end
