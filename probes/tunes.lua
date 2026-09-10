-- tunes.lua -- every tune this port actually starts.
--
-- The driver at $5B5A walks a 4-byte-per-note list through a pointer held
-- in a three-byte zero-page state block per channel: $95 (gate, lo, hi)
-- for one channel and $98 for the other. Starting a tune means writing a
-- fresh pointer into $96/$97 or $99/$9A; advancing one just adds 4. So a
-- write to the high byte that is not the driver's own +4 step is a tune
-- being started, and the low/high pair is which tune.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local OUT=os.getenv("A7800_TUNE_LOG") or "tunes.log"
local starts,order={},{}
local F=0
emu.register_frame_done(function() F=F+1 end)
local function watch(lo,hi,name)
  return mem:install_write_tap(hi,hi,name,function(off,data,mask)
    local pc=M.devices[":maincpu"].state["PC"].value
    -- $5B8E/$5B95 is the driver stepping its own pointer; anything else
    -- is somebody handing the channel a new tune
    if pc<0x5B80 or pc>0x5B99 then
      local a=mem:read_u8(lo)|(data<<8)
      local k=string.format("%s $%04X",name,a)
      if not starts[k] then starts[k]={0,pc,F}; order[#order+1]=k end
      starts[k][1]=starts[k][1]+1
    end
    return data
  end)
end
TAPS={watch(0x96,0x97,"chan0"), watch(0x99,0x9A,"chan1")}
local function dump()
  local f=io.open(OUT,"w")
  f:write(string.format("# frames %d\n# tune  plays  first_pc  first_frame\n",F))
  for _,k in ipairs(order) do
    local v=starts[k]
    f:write(string.format("%s  %d  $%04X  %d\n",k,v[1],v[2],v[3]))
  end
  f:close()
end
if emu.add_machine_stop_notifier then TUNE_STOPPER=emu.add_machine_stop_notifier(dump)
else emu.register_stop(dump) end
