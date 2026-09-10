-- freeram2.lua -- freeram.lua's question over a wider candidate set, driven
-- by a recorded session instead of hand play. Same caveats as freeram.lua:
-- zero writes is evidence, not proof. $E8 is the positive control.
local M=(type(manager.machine)=="function") and manager:machine() or manager.machine
local mem=M.devices[":maincpu"].spaces["program"]
local OUT=os.getenv("A7800_FREERAM_LOG") or "freeram2.log"
local WATCH={}
for a=0xD0,0xFF do WATCH[#WATCH+1]=a end
local writes,firstpc={},{}
for _,a in ipairs(WATCH) do writes[a]=0 end
TAPS={}
for _,addr in ipairs(WATCH) do
  local a=addr
  TAPS[#TAPS+1]=mem:install_write_tap(a,a,"freeram2",function(off,data,mask)
    writes[a]=writes[a]+1
    if not firstpc[a] then firstpc[a]=M.devices[":maincpu"].state["PC"].value end
    return data
  end)
end
local frames=0
emu.register_frame_done(function() frames=frames+1 end)
local function dump()
  local f=io.open(OUT,"w")
  f:write(string.format("# frames %d\n# addr writes first_pc\n",frames))
  for _,a in ipairs(WATCH) do
    f:write(string.format("%02X %d %04X\n",a,writes[a],firstpc[a] or 0))
  end
  f:close()
end
-- held in a global: the notifier is collected if its return value is
-- dropped, and the symptom is a run that looks fine and writes nothing
if emu.add_machine_stop_notifier then
  FREERAM2_STOPPER = emu.add_machine_stop_notifier(dump)
else
  emu.register_stop(dump)
end
