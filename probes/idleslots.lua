-- idleslots.lua -- option 5's real question, which is not "is a slot ever
-- used" but "how often is it parked on the no-op right now".
--
-- entityslots.lua asks whether a slot did anything across a whole session,
-- and over a full playthrough the answer is always yes for all nine. The
-- optimisation it was meant to decide is a per-round runtime test, so the
-- number that matters is the fraction of rounds a slot holds w_9908 -- and
-- the distribution of how many of the nine are parked at once.
--
-- Counted only inside a hall (stage 1-6), so the title, the attract demo
-- and the ending do not dilute it.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local OUT=os.getenv("A7800_IDLE_LOG") or "idleslots.log"
local SLOTS={0x18AD,0x1878,0x18AF,0x1897,0x18B1,0x18C5,0x18C7,0x18DF,0x18C9}
local NOOP=0x9908
local idle,hist,frames={},{},0
for i=1,#SLOTS do idle[i]=0 end
for n=0,#SLOTS do hist[n]=0 end
emu.register_frame_done(function()
  local stage=mem:read_u8(0x18AA)
  if stage<1 or stage>6 then return end
  frames=frames+1
  local n=0
  for i,a in ipairs(SLOTS) do
    if mem:read_u16(a)==NOOP then idle[i]=idle[i]+1; n=n+1 end
  end
  hist[n]=hist[n]+1
end)
local function dump()
  local f=io.open(OUT,"w")
  f:write(string.format("# frames in a hall: %d\n",frames))
  f:write("# slot addr parked_on_w_9908 pct\n")
  local tot=0
  for i,a in ipairs(SLOTS) do
    tot=tot+idle[i]
    f:write(string.format("slot %d %04X %d %.1f\n",i,a,idle[i],
      frames>0 and 100*idle[i]/frames or 0))
  end
  f:write(string.format("# mean parked slots per round: %.2f of %d\n",
    frames>0 and tot/frames or 0,#SLOTS))
  f:write("# how many of the nine are parked at once\n")
  for n=0,#SLOTS do
    f:write(string.format("parked %d %d %.1f\n",n,hist[n],
      frames>0 and 100*hist[n]/frames or 0))
  end
  f:close()
end
if emu.add_machine_stop_notifier then IDLE_STOPPER=emu.add_machine_stop_notifier(dump)
else emu.register_stop(dump) end
