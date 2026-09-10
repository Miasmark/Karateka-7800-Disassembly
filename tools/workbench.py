#!/usr/bin/env python3
"""
One place to open a cartridge and get at everything inside it.

    python tools/workbench.py game.a78

The toolkit is a dozen commands that each want a space, a base address and a
format. That is the right shape for the tools and the wrong shape for the first
hour with an unfamiliar ROM, where the question is simply "what is in here, and
can I see it?"

So this reads the header, finds what it can, and lists it. Every row is a thing
you can open: artwork goes to `spriteedit`, music to the tracker, and both are
launched with the space, base and format already filled in -- which is the part
that is tedious to get right by hand and silent when you get it wrong.

It is a launcher, not a new tool. Nothing here reimplements anything: the scan
is `assets.py`, the editors are the editors, and each runs in its own process
on its own port so that closing one does not take the others with it.
"""
import argparse
import io
import json
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cart as cart_module

ROM = None
CART = None
MANIFEST = None
CHILDREN = {}          # port -> (Popen, description)
NEXT_PORT = [8140]


def cart_info():
    """What the header and the mapper say, as data rather than a paragraph."""
    c = CART
    info = dict(c.info or {})
    spaces = []
    for start, end, kind, arg in c._region:
        if kind == "fixed":
            spaces.append({"name": c._fixed_name(arg), "start": start,
                           "end": end, "kind": "fixed"})
        elif kind == "window":
            for i in range(c.map.nwindow):
                spaces.append({"name": "b%d" % i, "start": start, "end": end,
                               "kind": "window"})
        else:
            spaces.append({"name": "(ram)", "start": start, "end": end,
                           "kind": "ram"})
    return {
        "rom": os.path.basename(ROM),
        "title": info.get("title", ""),
        "size": len(c.rom),
        "cart_type": info.get("cart_type", 0),
        "flags": info.get("flags", []),
        "region": info.get("region", "?"),
        "mapper": c.map.name,
        "note": getattr(c.map, "note", ""),
        "bankset": c.bankset,
        "side": getattr(c, "side", "sally"),
        "pokeys": ["$%04X" % b for b in c.pokeys()],
        "chip": "pokey" if c.pokeys() else "tia",
        "spaces": spaces,
        "vectors": {k: "$%04X" % v for k, v in c.vectors().items()},
        "warnings": list(c.warnings),
        "format": format_for(ROM),
        "format_guessed": format_is_guess(ROM),
    }


def format_for(rom):
    """The shipped format file that describes this cartridge, if any."""
    try:
        sys.path.insert(0, HERE)
        import trackeredit
        f = trackeredit.find_format(rom)
        return os.path.basename(f) if f else None
    except Exception:                                        # noqa: BLE001
        return None


def format_is_guess(rom):
    """Whether the format describing this cartridge was worked out by ear.

    Worth reporting separately. A guessed format plays, which makes it look
    exactly as authoritative as one written from the player's code, and it is
    not -- it covers one stretch of notes somebody listened to, not the
    cartridge's music.
    """
    try:
        sys.path.insert(0, HERE)
        import trackeredit
        f = trackeredit.find_format(rom)
        if not f:
            return False
        return bool(json.load(io.open(f, encoding="utf-8")).get("guessed"))
    except Exception:                                        # noqa: BLE001
        return False


def scan(config=None, ram=None, dll=None):
    """Run assets.py over the cartridge and keep what it found."""
    global MANIFEST
    import assets
    import audiotrace
    an = audiotrace.analyse(ROM, config)
    cart = an.cart
    audio = []
    for g in audiotrace.cluster(audiotrace.find_writers(an, cart)):
        for t in audiotrace.tables_in(g):
            audio.append({"space": g["space"], "addr": t["addr"],
                          "regs": t["regs"], "how": t["how"]})
    gw = assets.graphics_writers(an)
    lists = assets.constant_pairs(gw)
    seen, hits = set(), []
    for sp, addr in lists:
        hits.extend(assets.walk_lists(cart, sp, addr, seen))
    if ram and dll is not None:
        try:
            hits.extend(assets.from_ram_dump(cart, ram, 0x1800, dll))
        except ValueError:
            pass
    chars = assets.charbases(gw)
    MANIFEST = {
        "graphics": assets.collect_graphics(hits, chars),
        "audio": assets.collect_audio(audio),
        "palettes": assets.palette_writes(an),
        "display_lists": ["%s:%04X" % (sp, a) for sp, a in lists],
        "in_ram": [a for _sp, a in lists if a < 0x4000],
    }
    return MANIFEST


def manifest_file():
    """The scan on disk, so a launched editor can read the palettes from it."""
    import tempfile
    if not MANIFEST:
        return None
    path = os.path.join(tempfile.gettempdir(), "a7800-workbench-assets.json")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(MANIFEST))
    return path


def free_port(start):
    """The first port from `start` that nothing is listening on.

    Not just `start + n`. Something else may already hold it -- an editor from
    a previous run, or another copy of this -- and a child that cannot bind
    looks exactly like a child that crashed. Ask the OS instead of assuming.
    """
    import socket
    for port in range(start, start + 200):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free port in %d-%d" % (start, start + 200))


def launch(tool, args, what):
    """Start one of the editors on its own port and hand back its address."""
    port = free_port(NEXT_PORT[0])
    NEXT_PORT[0] = port + 1
    cmd = [sys.executable, os.path.join(HERE, tool), ROM] + list(args) + \
          ["--no-browser", "--port", str(port)]
    if tool == "trackeredit.py":
        # it takes a song, not a cartridge; the caller passes the path
        cmd = [sys.executable, os.path.join(HERE, tool)] + list(args) + \
              ["--no-browser", "--port", str(port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    CHILDREN[port] = (proc, what)
    return {"port": port, "url": "http://127.0.0.1:%d/" % port, "what": what}


def running():
    out = []
    for port, (proc, what) in sorted(CHILDREN.items()):
        alive = proc.poll() is None
        out.append({"port": port, "what": what, "alive": alive,
                    "url": "http://127.0.0.1:%d/" % port})
    return out


def stop_all():
    for _port, (proc, _what) in CHILDREN.items():
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if ctype == "application/json":
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if p == "/api/info":
                return self._send(200, cart_info())
            if p == "/api/manifest":
                return self._send(200, MANIFEST or {"graphics": [], "audio": []})
            if p == "/api/running":
                return self._send(200, {"tools": running()})
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        try:
            if self.path == "/api/scan":
                return self._send(200, scan(body.get("config") or None,
                                            body.get("ram") or None,
                                            body.get("dll")))
            if self.path == "/api/open":
                kind = body.get("kind")
                if kind == "sprite":
                    args = ["--space", body["space"], "--base",
                            str(int(body["base"])), "--height",
                            str(int(body.get("height", 8))), "--width",
                            str(int(body.get("width", 1))), "--mode",
                            str(body.get("mode", "160"))]
                    if CART.bankset:
                        args += ["--side", body.get("side", "sally")]
                    mf = manifest_file()
                    if mf:
                        # so the picker can offer the palettes this cartridge
                        # writes, rather than only greys
                        args += ["--palette-from", mf]
                    return self._send(200, launch(
                        "spriteedit.py", args,
                        "sprites %s:$%04X" % (body["space"], int(body["base"]))))
                if kind == "explore":
                    # Where the sound data is, opened in the tool that works
                    # out what it means. The scan already knows the address;
                    # retyping it into a second tool is the step that made
                    # people give up.
                    loc = str(body.get("loc") or "")
                    sp, _, a = loc.partition(":")
                    if not sp or not a:
                        raise ValueError("explore needs a location like f6:76F6")
                    args = ["--at", loc, "--chip",
                            body.get("chip") or ("pokey" if CART.pokeys()
                                                 else "tia")]
                    return self._send(200, launch(
                        "explore.py", args, "explore %s" % loc))
                if kind == "tracker":
                    song = body.get("song") or ROM
                    extra = []
                    if body.get("song_number") is not None:
                        extra = ["--song-number", str(int(body["song_number"]))]
                    return self._send(200, launch(
                        "trackeredit.py", [song] + extra,
                        "tracker %s" % os.path.basename(song)))
                return self._send(400, {"error": "unknown kind %r" % kind})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})


PAGE = r"""<!doctype html>
<meta charset="utf-8"><title>7800 workbench</title>
<style>
 :root{--bg:#15151b;--fg:#e6e6ee;--dim:#8b8b9c;--line:#2c2c38;--accent:#d8a657}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}
 header{padding:12px 18px;border-bottom:1px solid var(--line);
        display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
 h1{font-size:14px;margin:0;font-weight:600}
 h2{font-size:12px;margin:18px 0 6px;color:var(--accent);font-weight:600;
    text-transform:uppercase;letter-spacing:.08em}
 .muted{color:var(--dim)}
 main{padding:12px 18px 40px;max-width:1100px}
 table{border-collapse:collapse;width:100%;margin-bottom:4px}
 th{text-align:left;color:var(--dim);font-weight:500;padding:2px 10px 2px 0;
    border-bottom:1px solid var(--line)}
 td{padding:2px 10px 2px 0;border-bottom:1px solid #20202a;vertical-align:top}
 button{background:#22222c;color:var(--fg);border:1px solid var(--line);
        border-radius:3px;padding:2px 9px;font:inherit;cursor:pointer}
 button:hover{border-color:var(--accent)}
 .warn{color:#e0a458}
 .err{color:#e06c75}
 .pill{border:1px solid var(--line);border-radius:9px;padding:0 8px;
       color:var(--dim)}
 a{color:var(--accent)}
 #msg{min-height:20px}
</style>
<header>
  <h1>7800 workbench</h1>
  <span class="muted" id="what"></span>
  <span style="flex:1"></span>
  <button onclick="scan()">scan for assets</button>
  <button onclick="openTracker()">open tracker</button>
</header>
<main>
  <div id="info"></div>
  <div id="msg" class="muted"></div>
  <div id="assets"></div>
  <div id="tools"></div>
</main>
<script>
const $=id=>document.getElementById(id);
let INFO=null;

async function load(){
  INFO=await (await fetch('/api/info')).json();
  $('what').textContent=INFO.rom+'  '+INFO.mapper+'  '+
    Math.round(INFO.size/1024)+'K  '+INFO.region+'  '+INFO.chip.toUpperCase()+
    (INFO.pokeys.length?' at '+INFO.pokeys.join(', '):'');
  let h='<h2>cartridge</h2><table>';
  h+=row('title',INFO.title||'(none)');
  h+=row('cart type','$'+INFO.cart_type.toString(16).toUpperCase().padStart(4,'0')+
        (INFO.flags.length?'  '+INFO.flags.join(', '):''));
  h+=row('mapper',INFO.mapper+(INFO.note?'  ('+INFO.note+')':''));
  h+=row('vectors',Object.entries(INFO.vectors).map(x=>x[0]+' '+x[1]).join('  '));
  h+=row('spaces',INFO.spaces.map(s=>s.name).join(' '));
  h+=row('music', INFO.format
    ? (INFO.format_guessed
       ? '<b>'+esc(INFO.format)+'</b> &mdash; a reading worked out by ear, '+
         'not a description of the player. It plays, so you can check it; '+
         'it covers one stretch of notes, not the cartridge&rsquo;s music.'
       : 'readable from the ROM with <b>'+esc(INFO.format)+'</b> &mdash; '+
         'the tracker opens it without an emulator')
    : '<span class="muted">no format file describes this cartridge; the '+
      'tracker will record it in an emulator instead</span>');
  if(INFO.bankset)
    h+=row('bankset','two parallel sets; editing the <b>'+INFO.side+
           '</b> half. Artwork is normally the maria one.');
  h+='</table>';
  for(const w of INFO.warnings) h+='<div class="warn">'+esc(w)+'</div>';
  $('info').innerHTML=h;
  refresh();
}
function row(k,v){return '<tr><td class="muted" style="width:120px">'+k+
  '</td><td>'+v+'</td></tr>';}
function esc(s){return String(s).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));}

async function scan(){
  msg('scanning -- this disassembles the ROM, so give it a moment');
  const r=await fetch('/api/scan',{method:'POST',body:'{}'});
  const j=await r.json();
  if(j.error){msg(j.error,true);return;}
  showAssets(j); msg('');
}
function showAssets(m){
  let h='';
  const sure=m.graphics.filter(g=>g.certain);
  const maybe=m.graphics.filter(g=>!g.certain);
  h+='<h2>graphics ('+sure.length+' placed';
  if(maybe.length) h+=', '+maybe.length+' bank-ambiguous';
  h+=')</h2>';
  if(!sure.length && !maybe.length)
    h+='<div class="muted">Nothing placed. On most games the display list is '+
       'built in RAM, so a static scan stops there -- capture one with '+
       'probes/dumpdl.lua and pass it to assets.py.</div>';
  if(sure.length){
    h+='<table><tr><th>where</th><th>shape</th><th>evidence</th><th></th></tr>';
    for(const g of sure){
      const [sp,ad]=g.loc.split(':');
      const w=g.width||1;
      h+='<tr><td>'+g.loc+'</td><td>'+(g.width?g.width+' bytes wide':'character set')+
         '</td><td class="muted">'+esc(g.source)+'</td><td>'+
         '<button onclick="openSprite(\''+sp+'\',0x'+ad+','+w+')">edit</button>'+
         '</td></tr>';
    }
    h+='</table>';
  }
  if(maybe.length){
    h+='<div class="muted">'+maybe.length+' more sit in the paged window, where '+
       'the capture does not record which bank MARIA read. They are candidates, '+
       'not findings -- open one by hand if you know the bank.</div>';
  }
  h+='<h2>audio ('+m.audio.length+' tables)</h2>';
  if(m.audio.length){
    h+='<table><tr><th>where</th><th>feeds</th><th>how</th><th></th></tr>';
    for(const a of m.audio)
      h+='<tr><td>'+a.loc+'</td><td>'+a.regs.join(' ')+'</td>'+
         '<td class="muted">'+esc(a.how)+'</td>'+
         '<td><button onclick="openExplore(&quot;'+esc(a.loc)+'&quot;)">'+
         'explore</button></td></tr>';
    h+='</table>';
    // Finding the tables and being able to read the songs are two different
    // questions, and reporting both without saying so reads as a
    // contradiction: "3 audio tables" next to "no format file".
    if(INFO.format){
      h+='<div class="muted">Those are the tables the sound code reads. '+
         '<b>'+esc(INFO.format)+'</b> also describes how this player arranges '+
         'them into songs, so the tracker opens them straight from the ROM.'+
         '</div>';
    } else {
      h+='<div class="muted"><b>Finding these is not the same as being able to '+
         'read the songs.</b> These addresses are <i>where</i> the sound data '+
         'is &mdash; the tables the player reads for waveforms, pitches and '+
         'control values. What the bytes <i>mean</i> as music &mdash; which '+
         'notes, in what order, for how long &mdash; belongs to the player '+
         'and no file in <code>formats/</code> describes this one. '+
         'Writing one is the reverse engineering; <code>templates/format.json'+
         '</code> is the starting point. Until then the tracker records the '+
         'game instead, which needs no format at all.</div>';
    }
  } else {
    h+='<div class="muted">No audio tables reached. The player is usually '+
       'behind an indirect jump; the tracker still works, because it watches '+
       'the running machine.</div>';
  }
  $('assets').innerHTML=h;
}

async function openSprite(space,base,width){
  const r=await fetch('/api/open',{method:'POST',body:JSON.stringify(
    {kind:'sprite',space:space,base:base,width:width,height:8,
     side:INFO.bankset?'maria':'sally'})});
  const j=await r.json();
  if(j.error){msg(j.error,true);return;}
  window.open(j.url,'_blank'); setTimeout(refresh,600);
}
async function openExplore(loc){
  msg('opening the format explorer at '+loc);
  const r=await fetch('/api/open',{method:'POST',body:JSON.stringify(
    {kind:'explore',loc:loc,chip:INFO.chip})});
  const j=await r.json();
  if(j.error){msg(j.error,true);return;}
  window.open(j.url,'_blank'); msg(''); setTimeout(refresh,600);
}
async function openTracker(){
  msg(INFO.format
      ? 'reading the songs out of the ROM with ' + INFO.format + ' -- no emulator'
      : 'no format file describes this cartridge, so it will be recorded in '+
        'an emulator; that takes about a minute');
  const r=await fetch('/api/open',{method:'POST',body:JSON.stringify({kind:'tracker'})});
  const j=await r.json();
  if(j.error){msg(j.error,true);return;}
  msg('tracker starting at '+j.url);
  setTimeout(()=>{window.open(j.url,'_blank');refresh();},2500);
}
async function refresh(){
  const j=await (await fetch('/api/running')).json();
  if(!j.tools.length){$('tools').innerHTML='';return;}
  let h='<h2>open</h2><table>';
  for(const t of j.tools)
    h+='<tr><td>'+esc(t.what)+'</td><td>'+
       (t.alive?'<a href="'+t.url+'" target="_blank">'+t.url+'</a>'
              :'<span class="muted">closed</span>')+'</td></tr>';
  $('tools').innerHTML=h+'</table>';
}
function msg(s,bad){const m=$('msg');m.textContent=s;m.className=bad?'err':'muted';}
setInterval(refresh,4000);
load();
</script>
"""


def main():
    global ROM, CART
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--side", choices=["sally", "maria"], default="sally",
                    help="bankset cartridges: which parallel set to work on")
    ap.add_argument("--port", type=int, default=8120)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    ROM = os.path.abspath(args.rom)
    try:
        CART = cart_module.Cart(ROM, side=args.side)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("%s -- %s, %dK, %s"
          % (os.path.basename(ROM), CART.map.name, len(CART.rom) // 1024,
             "POKEY" if CART.pokeys() else "TIA"))
    for w in CART.warnings:
        print("  note: %s" % w)
    print("open %s" % url)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        # The editors are separate processes on their own ports. Leaving them
        # running after the workbench closes would hold those ports and look
        # like a stale server on the next run, which is a genuinely confusing
        # way to waste an afternoon.
        stop_all()
        print("stopped %d tool%s" % (len(CHILDREN),
                                     "" if len(CHILDREN) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
