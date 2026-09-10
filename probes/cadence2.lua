-- cadence2.lua -- an ordered event log of one near-world head and the
-- knockback cycle byte, so the pixels belonging to a single hit can be told
-- apart from the pixels belonging to walking.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local OUT=os.getenv("A7800_CADENCE_LOG") or "cadence2.log"
local f=io.open(OUT,"w")
local F=0
emu.register_frame_done(function() F=F+1 end)
TAPS={}
TAPS[1]=mem:install_write_tap(0x2423,0x2423,"near",function(o,d,m)
  f:write(string.format("%d near %02X\n",F,d)); return d end)
TAPS[2]=mem:install_write_tap(0xF7,0xF7,"phase",function(o,d,m)
  f:write(string.format("%d PHASE-> %02X\n",F,d)); return d end)
TAPS[3]=mem:install_write_tap(0x188C,0x188C,"goonx",function(o,d,m)
  f:write(string.format("%d goonx %02X\n",F,d)); return d end)
TAPS[4]=mem:install_write_tap(0xF8,0xF8,"phaseb",function(o,d,m)
  f:write(string.format("%d PHASEB-> %02X\n",F,d)); return d end)
if emu.add_machine_stop_notifier then
  CAD2_STOPPER=emu.add_machine_stop_notifier(function() f:flush(); f:close() end)
end
