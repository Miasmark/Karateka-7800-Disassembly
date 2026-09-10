-- tunewhen.lua -- what is on screen when each tune starts.
-- Taps the channel pointer high bytes, skips the driver's own +4 step, and
-- on a genuine tune start logs the game state and takes a screenshot.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local f=io.open(os.getenv("A7800_TUNE_LOG") or "tunewhen.log","w")
f:write("shot frame tune stage section oppHP playerHP stance slot2 slot4\n")
local F,N=0,0
local pending=nil
emu.register_frame_done(function()
  F=F+1
  if pending then
    pcall(function() M.video:snapshot() end)
    f:write(pending)
    pending=nil
  end
end)
local function watch(lo,hi,name)
  return mem:install_write_tap(hi,hi,name,function(off,data,mask)
    local pc=M.devices[":maincpu"].state["PC"].value
    if pc<0x5B80 or pc>0x5B99 then
      local a=mem:read_u8(lo)|(data<<8)
      if name=="chan0" then       -- one line per event, keyed on channel 0,
        N=N+1                     -- so a two-channel tune logs once
        pending=string.format("%04d %d $%04X %d %d %d %d %d %04X %04X\n",
          N,F,a,mem:read_u8(0x18AA),mem:read_u8(0x18DC),mem:read_u8(0x18C2),
          mem:read_u8(0x18BF),mem:read_u8(0x187C),
          mem:read_u8(0x1878)|(mem:read_u8(0x1879)<<8),
          mem:read_u8(0x1897)|(mem:read_u8(0x1898)<<8))
      end
    end
    return data
  end)
end
TAPS={watch(0x96,0x97,"chan0"), watch(0x99,0x9A,"chan1")}
if emu.add_machine_stop_notifier then
  TW_STOPPER=emu.add_machine_stop_notifier(function() f:flush(); f:close() end)
end
