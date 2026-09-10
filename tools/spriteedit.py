#!/usr/bin/env python3
"""
Edit a cartridge's artwork in the browser, and write it back.

    python tools/spriteedit.py game.a78 --space b3 --base 0xA000
    python tools/spriteedit.py game.a78 --manifest assets.json

`gfx.py` draws artwork so you can find it. This lets you change it. It reads
the bytes out of the ROM in whichever pixel format you tell it, paints them as
a grid you can click, and writes the result back into a copy of the image.

## Where the bytes are

MARIA reads graphics **line-planar**: line 0 of every cell on one page, line 1
on the next. So the byte holding line `l`, column `x` of cell `c` is

    base + c * width + l * stride + x

with `stride` 256 for anything MARIA fetches directly, which is nearly
everything. A character set is the same shape with `width` 1: page CHARBASE+0
holds line 0 of all 256 characters. That one formula covers both, which is why
this tool does not need to know whether it is looking at a font or a sprite
sheet.

## What it will not do

The same rule `songfmt.py` follows: **an edit may not touch a byte outside the
region you opened.** Before writing, the new image is diffed against the old
and any change outside the region aborts the save. Artwork sits next to code
and tables, and a paint program that quietly clips into the routine after it is
worse than no paint program.

It also will not guess the format. If the artwork comes out as noise, it is
being read in the wrong mode or at the wrong stride -- `--mode 320` and
`--stride` are there for that, and `docs/graphics.md` explains which is which.

## Colour

Greys by default, and that is a refusal rather than a placeholder: **the colours
are not in the artwork.** MARIA holds them in palette registers, a display-list
entry picks one of eight, and a game rewrites them per zone and per frame. So
the data cannot tell you what it looked like, and greys at least show the true
pixel indices.

What you can do is choose. Right-click a swatch for the 7800's 16x16 grid of
hues and luminances, or pass `--palette 24,76,7C`. And `--palette-from` takes an
`assets.py` manifest and offers **the palettes the cartridge's own code writes
as constants** -- not the answer, but a short list of real candidates rather
than 16 million guesses. The workbench passes it automatically after a scan.

Index 0 is transparent on the hardware: MARIA shows the background through it.
It is drawn as the background here and cannot be given a colour of its own.

## Drawing

pen, flood fill, line, rectangle and ellipse (outline or filled); undo, copy,
paste, clear, flip either way, and shift a cell a pixel at a time with wrap.
Keys: `ctrl+Z` `ctrl+C` `ctrl+V`, `0`-`3` for colours, `[` and `]` to step
through cells.

Every tool builds the whole cell in the browser and posts it in one write.
Painting a pixel at a time over HTTP was fine for a pen and hopeless for a
flood fill, and it also makes undo trivial: keep the previous grid and post it
back.
"""
import argparse
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart as cart_module
import palette as palette_mod

# Pixel formats: bits per pixel, and how many pixels a byte carries.
#
# "320" here is 320A and 320D, which are one bit per pixel, eight to a byte.
# **320B and 320C are not this shape** -- they reach four colours at 320
# resolution by pairing bytes, so reading them as 320A gives a picture that is
# the right size and the wrong image. They are not modelled; if artwork comes
# out looking like the right shapes in the wrong colours, that is the likely
# reason.
MODES = {"160": (2, 4), "320": (1, 8)}

CART = None
DATA = None            # bytearray of the whole image, edited in place
REGION = None
DIRTY = False
PATH = None


class Region(object):
    """The window of artwork being edited, and where its bytes live."""

    def __init__(self, cart, space, base, width, height, count, stride, mode,
                 descending=True):
        self.cart, self.space, self.base = cart, space, base
        self.width, self.height, self.count = width, height, count
        self.stride, self.mode = stride, mode
        self.descending = descending
        self.bpp, self.ppb = MODES[mode]

    def addr(self, cell, line, col):
        """Where line `line` of cell `cell` lives.

        **MARIA's zone offset counts DOWN.** The first scanline of a zone reads
        the highest page and the last reads the base, so the top line of a
        sprite is at `base + (height-1)*stride`, not at `base`. Reading pages in
        ascending order renders every sprite upside down -- which is exactly
        what this did until someone looked at the result and said so.

        `descending=False` is for data that is not a MARIA zone: a contiguous
        blob, or a table you are viewing as pixels. There the lines really do
        ascend.
        """
        n = (self.height - 1 - line) if self.descending else line
        return self.base + cell * self.width + n * self.stride + col

    def offsets(self):
        """Every file offset this region owns -- the safety net for saving."""
        out = set()
        for c in range(self.count):
            for l in range(self.height):
                for x in range(self.width):
                    try:
                        out.add(self.file_offset(self.addr(c, l, x)))
                    except ValueError:
                        pass
        return out

    def file_offset(self, addr):
        """Where this CPU address lives in the file, header included.

        `cart._offset` is the same mapping the assembler and `songfmt` use, so
        an edit lands exactly where a rebuild would put it.
        """
        base = self.cart.base_of(self.space)
        size = self.cart.size_of(self.space)
        if not (base <= addr < base + size):
            raise ValueError("$%04X is outside %s" % (addr, self.space))
        head = 128 if self.cart.header_bytes else 0
        return head + self.cart._offset(self.space, addr)

    def pixels(self, cell):
        """One cell as height rows of (width * ppb) pixel values."""
        rows = []
        for l in range(self.height):
            row = []
            for x in range(self.width):
                try:
                    b = DATA[self.file_offset(self.addr(cell, l, x))]
                except (ValueError, IndexError):
                    b = 0
                for p in range(self.ppb):
                    shift = (self.ppb - 1 - p) * self.bpp
                    row.append((b >> shift) & ((1 << self.bpp) - 1))
            rows.append(row)
        return rows

    def set_pixel(self, cell, line, px, value):
        col, within = divmod(px, self.ppb)
        off = self.file_offset(self.addr(cell, line, col))
        shift = (self.ppb - 1 - within) * self.bpp
        mask = ((1 << self.bpp) - 1) << shift
        DATA[off] = (DATA[off] & ~mask & 0xFF) | ((value << shift) & mask)
        return DATA[off]

    def set_cell(self, cell, rows):
        """Write a whole cell at once.

        Shapes, fills, paste and undo all replace a cell wholesale rather than
        walking pixels one at a time. Doing it in a single write keeps the
        drawing tools on the browser side, where they belong, and keeps this
        side to one job: putting bytes where the cartridge keeps them.
        """
        wide = self.width * self.ppb
        if len(rows) != self.height:
            raise ValueError("this cell has %d lines, got %d"
                             % (self.height, len(rows)))
        top = (1 << self.bpp) - 1
        for l, row in enumerate(rows):
            if len(row) != wide:
                raise ValueError("line %d has %d pixels, expected %d"
                                 % (l, len(row), wide))
            for x, v in enumerate(row):
                self.set_pixel(cell, l, x, int(v) & top)
        return self.pixels(cell)


def region_json():
    r = REGION
    return {
        "space": r.space, "base": r.base, "width": r.width,
        "height": r.height, "count": r.count, "stride": r.stride,
        "mode": r.mode, "bpp": r.bpp, "ppb": r.ppb,
        "descending": r.descending,
        "colours": 1 << r.bpp,
        "cells": [r.pixels(c) for c in range(r.count)],
        "palette": PALETTE,
        "palette_bytes": PALETTE_BYTES,
        "candidates": CANDIDATES,
        "dirty": DIRTY,
        "rom": os.path.basename(PATH or ""),
    }


# The four entries a 160-mode pixel can select. Index 0 is transparent on the
# hardware -- MARIA shows the background colour through it -- so it is drawn as
# the background here rather than as a colour of its own.
#
# Greys by default, and that is a deliberate refusal rather than a placeholder:
# the colours are MARIA registers, written at run time and often rewritten per
# zone, so the artwork does not carry them. Greys show the real pixel indices.
# A palette is something you choose, or take from `--palette-from`.
PALETTE_BYTES = [None, None, None, None]
PALETTE = ["#14141a", "#69697a", "#afafba", "#f5f5fa"]
GREYS = list(PALETTE)
CANDIDATES = []


def set_palette(byts):
    """Apply four 7800 colour bytes; None anywhere falls back to the greys."""
    global PALETTE, PALETTE_BYTES
    out = []
    for i in range(4):
        b = byts[i] if i < len(byts) else None
        if b is None:
            out.append(GREYS[i])
        else:
            r, g, bl = palette_mod.ntsc7800(int(b) & 0xFF)
            out.append("#%02x%02x%02x" % (r, g, bl))
    PALETTE_BYTES = [None if i >= len(byts) else byts[i] for i in range(4)]
    PALETTE = out
    return PALETTE


def save(out_path):
    """Write the edited image, refusing to touch anything outside the region."""
    original = open(PATH, "rb").read()
    owned = REGION.offsets()
    changed = [i for i in range(min(len(original), len(DATA)))
               if original[i] != DATA[i]]
    stray = [i for i in changed if i not in owned]
    if stray:
        raise ValueError(
            "refusing to save: %d byte%s outside the region you opened would "
            "change (first at file offset $%X). That is a bug in this tool, "
            "not something to override."
            % (len(stray), "" if len(stray) == 1 else "s", stray[0]))
    with open(out_path, "wb") as f:
        f.write(bytes(DATA))
    return {"path": out_path, "changed": len(changed),
            "owned": len(owned)}


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
            if p == "/api/region":
                return self._send(200, region_json())
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})

    def do_POST(self):
        global DIRTY
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        try:
            if self.path == "/api/pixel":
                REGION.set_pixel(int(body["cell"]), int(body["line"]),
                                 int(body["px"]), int(body["value"]))
                DIRTY = True
                return self._send(200, {"ok": True,
                                        "cell": REGION.pixels(int(body["cell"]))})
            if self.path == "/api/cell":
                px = REGION.set_cell(int(body["cell"]), body["rows"])
                DIRTY = True
                return self._send(200, {"ok": True, "cell": px})
            if self.path == "/api/palette":
                cols = body.get("colours") or []
                set_palette([None if c is None else int(c) for c in cols])
                return self._send(200, {"palette": PALETTE,
                                        "palette_bytes": PALETTE_BYTES})
            if self.path == "/api/save":
                out = body.get("path") or (os.path.splitext(PATH)[0]
                                           + "-edited.a78")
                r = save(out)
                DIRTY = False
                return self._send(200, r)
            if self.path == "/api/goto":
                REGION.base = int(body["base"])
                REGION.count = max(1, int(body.get("count", REGION.count)))
                REGION.height = max(1, int(body.get("height", REGION.height)))
                REGION.width = max(1, int(body.get("width", REGION.width)))
                REGION.stride = max(1, int(body.get("stride", REGION.stride)))
                if "descending" in body:
                    REGION.descending = bool(body["descending"])
                mode = str(body.get("mode", REGION.mode))
                if mode in MODES:
                    REGION.mode = mode
                    REGION.bpp, REGION.ppb = MODES[mode]
                return self._send(200, region_json())
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                                # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "no such thing"})


PAGE = r"""<!doctype html>
<meta charset="utf-8"><title>7800 sprite editor</title>
<style>
 :root{--bg:#15151b;--fg:#e6e6ee;--dim:#8b8b9c;--line:#2c2c38;--accent:#d8a657}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
 header{padding:10px 16px;border-bottom:1px solid var(--line);
        display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 h1{font-size:14px;margin:0;font-weight:600;letter-spacing:.02em}
 .muted{color:var(--dim)}
 label{color:var(--dim)}
 input{background:#0f0f14;color:var(--fg);border:1px solid var(--line);
       border-radius:3px;padding:3px 6px;width:82px;font:inherit}
 button{background:#22222c;color:var(--fg);border:1px solid var(--line);
        border-radius:3px;padding:4px 11px;font:inherit;cursor:pointer}
 button:hover{border-color:var(--accent)}
 main{display:flex;gap:18px;padding:14px 16px;align-items:flex-start}
 #sheetwrap{max-height:78vh;overflow:auto;border:1px solid var(--line);
            border-radius:4px;padding:6px;background:#101016}
 canvas{image-rendering:pixelated;display:block}
 #edit{border:1px solid var(--line);border-radius:4px;background:#101016}
 .pal{display:flex;gap:6px;margin:8px 0}
 .sw{width:26px;height:26px;border-radius:3px;border:2px solid transparent;
     cursor:pointer}
 .sw.on{border-color:var(--accent)}
 .col{display:flex;flex-direction:column;gap:6px}
 .tools{display:flex;gap:5px;flex-wrap:wrap}
 .tools button.on{border-color:var(--accent);color:var(--accent)}
 #picker{display:none;border:1px solid var(--line);border-radius:4px;
         padding:8px;background:#101016;margin:4px 0}
 #picker .grid{display:grid;grid-template-columns:repeat(16,16px);gap:1px;
               margin-top:6px}
 #picker .cell{width:16px;height:16px;cursor:pointer;border-radius:2px}
 #picker .cell:hover{outline:2px solid var(--accent)}
 #picker .cands{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0}
 #picker .cands button{display:flex;gap:3px;align-items:center}
 .chip{width:11px;height:11px;border-radius:2px;display:inline-block}
 #msg{min-height:18px}
 .err{color:#e06c75}
</style>
<header>
  <h1>7800 sprite editor</h1>
  <span class="muted" id="what"></span>
  <span style="flex:1"></span>
  <label>base <input id="base"></label>
  <label>w <input id="w" style="width:48px"></label>
  <label>h <input id="h" style="width:48px"></label>
  <label>n <input id="n" style="width:56px"></label>
  <label>stride <input id="stride" style="width:56px"></label>
  <label>mode <input id="mode" style="width:48px"></label>
  <button onclick="go()">go</button>
  <button onclick="save()">save</button>
</header>
<main>
  <div class="col">
    <div class="muted">sheet — click a cell</div>
    <div id="sheetwrap"><canvas id="sheet"></canvas></div>
  </div>
  <div class="col">
    <div class="tools" id="tools"></div>
    <div class="muted">cell <span id="cellno">0</span> — click or drag</div>
    <canvas id="edit" width="480" height="480"></canvas>
    <div class="pal" id="pal"></div>
    <div id="picker"></div>
    <div class="tools">
      <button onclick="undo()" title="ctrl+Z">undo</button>
      <button onclick="copyCell()" title="ctrl+C">copy</button>
      <button onclick="pasteCell()" title="ctrl+V">paste</button>
      <button onclick="clearCell()">clear</button>
      <button onclick="flip('h')">flip &#8596;</button>
      <button onclick="flip('v')">flip &#8597;</button>
      <button onclick="shift(0,-1)">&#8593;</button>
      <button onclick="shift(0,1)">&#8595;</button>
      <button onclick="shift(-1,0)">&#8592;</button>
      <button onclick="shift(1,0)">&#8594;</button>
    </div>
    <div id="msg" class="muted"></div>
  </div>
</main>
<script>
let R=null, sel=0, colour=1, painting=false;
let tool='pen', undoStack=[], clip=null, start=null, preview=null;
const TOOLS=[['pen','pen'],['fill','fill'],['line','line'],
             ['rect','rect'],['frect','rect fill'],['ellipse','ellipse'],
             ['fellipse','ellipse fill']];
const $=id=>document.getElementById(id);
const W=()=>R.width*R.ppb, H=()=>R.height;
const copyGrid=g=>g.map(r=>r.slice());

async function load(){ R=await (await fetch('/api/region')).json(); draw(); }

function fields(){
  $('base').value='0x'+R.base.toString(16).toUpperCase();
  $('w').value=R.width; $('h').value=R.height; $('n').value=R.count;
  $('stride').value=R.stride; $('mode').value=R.mode;
  $('what').textContent=R.rom+'  '+R.space+':$'+R.base.toString(16).toUpperCase()
    +'  '+R.count+' cells, '+W()+'x'+H()+'px';
}
function tools(){
  const t=$('tools'); t.innerHTML='';
  for(const pair of TOOLS){
    const b=document.createElement('button');
    b.textContent=pair[1];
    if(pair[0]===tool) b.className='on';
    b.onclick=()=>{tool=pair[0]; tools();};
    t.appendChild(b);
  }
}
/* --- palette ------------------------------------------------------------
   The colours are MARIA registers, not part of the artwork, so nothing in the
   data says what they should be. Greys show the true pixel indices; anything
   else is a choice. `--palette-from` offers the ones the cartridge's own code
   writes as constants, which is as close to an answer as a static tool gets. */
let picking = null;      /* which slot the colour grid is choosing for */

function palRGB(byte){
  /* the same NTSC approximation palette.py uses, close enough to choose by */
  const hue = (byte >> 4) & 15, lum = byte & 15;
  const y = lum / 15;
  if(hue === 0){ const v = Math.round(y * 255); return [v, v, v]; }
  const h = (((hue - 1) / 15) + 0.62) % 1.0;
  const s = 0.55 * (1 - Math.abs(y - 0.5)), v = Math.min(1, y + 0.25);
  const i = Math.floor(h * 6), f = h * 6 - i;
  const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  const r = [v,q,p,p,t,v][i % 6], g = [t,v,v,q,p,p][i % 6], b = [p,p,t,v,v,q][i % 6];
  return [Math.round(r*255), Math.round(g*255), Math.round(b*255)];
}
const hex = b => '#' + palRGB(b).map(v => v.toString(16).padStart(2,'0')).join('');

function pal(){
  const p = $('pal'); p.innerHTML = '';
  for(let i = 0; i < R.colours; i++){
    const d = document.createElement('div');
    d.className = 'sw' + (i === colour ? ' on' : '');
    d.style.background = R.palette[i] || '#000';
    const b = R.palette_bytes && R.palette_bytes[i];
    d.title = 'colour ' + i + (i === 0 ? ' (transparent -- shows the background)' : '')
            + (b == null ? '  [grey: no palette chosen]' : '  $' + b.toString(16).toUpperCase())
            + '\nclick to select, right-click to set its colour';
    d.onclick = () => { colour = i; pal(); };
    d.oncontextmenu = e => { e.preventDefault(); openPicker(i); };
    p.appendChild(d);
  }
  const b = document.createElement('button');
  b.textContent = 'palette';
  b.onclick = () => openPicker(colour);
  b.title = 'choose the colours these indices are drawn in';
  p.appendChild(b);
}

function openPicker(slot){
  picking = slot;
  const g = $('picker');
  let h = '<div class="muted">colour for index ' + slot +
          ' &mdash; 7800 has 16 hues by 16 luminances</div>';
  if(R.candidates && R.candidates.length){
    h += '<div class="muted" style="margin-top:6px">from this cartridge:</div><div class="cands">';
    for(const c of R.candidates){
      const cols = c.colours;
      const trio = [1,2,3].map(i => (cols[i] && cols[i][0]));
      if(trio.some(x => x == null)) continue;
      h += '<button onclick="useCandidate([' + trio.join(',') + '])" title="'+
           trio.map(x=>'$'+x.toString(16).toUpperCase()).join(' ')+'">'+
           (c.palette === null ? 'bg' : 'pal ' + c.palette) + ' ' +
           trio.map(x => '<span class="chip" style="background:' + hex(x) + '"></span>').join('') +
           '</button>';
    }
    h += '</div>';
  }
  h += '<div class="grid">';
  for(let lum = 0; lum < 16; lum++){
    for(let hue = 0; hue < 16; hue++){
      const b = (hue << 4) | lum;
      h += '<div class="cell" style="background:' + hex(b) + '" title="$' +
           b.toString(16).toUpperCase().padStart(2,'0') +
           '" onclick="pickColour(' + b + ')"></div>';
    }
  }
  h += '</div><div style="margin-top:8px">' +
       '<button onclick="pickColour(null)">grey (no palette)</button> ' +
       '<button onclick="closePicker()">close</button></div>';
  g.innerHTML = h;
  g.style.display = 'block';
}
function closePicker(){ picking = null; $('picker').style.display = 'none'; }

async function pickColour(byte){
  const cols = (R.palette_bytes || [null,null,null,null]).slice();
  if(byte === null){
    for(let i = 0; i < 4; i++) cols[i] = null;      /* whole palette back to greys */
  } else {
    cols[picking] = byte;
  }
  await applyPalette(cols);
  if(byte === null) closePicker();
}
async function useCandidate(trio){
  await applyPalette([null, trio[0], trio[1], trio[2]]);
  closePicker();
}
async function applyPalette(cols){
  const r = await fetch('/api/palette', {method:'POST',
    body: JSON.stringify({colours: cols})});
  const j = await r.json();
  if(j.error){ msg(j.error, true); return; }
  R.palette = j.palette; R.palette_bytes = j.palette_bytes;
  draw();
}

function draw(){
  fields(); pal(); tools();
  const cw=W(), ch=H(), cols=Math.min(16,R.count);
  const rows=Math.ceil(R.count/cols), z=Math.max(2,Math.floor(320/(cols*cw)));
  const s=$('sheet'); s.width=cols*cw*z; s.height=rows*ch*z;
  const g=s.getContext('2d'); g.imageSmoothingEnabled=false;
  for(let c=0;c<R.count;c++){
    const ox=(c%cols)*cw*z, oy=Math.floor(c/cols)*ch*z, px=R.cells[c];
    for(let y=0;y<ch;y++)for(let x=0;x<cw;x++){
      g.fillStyle=R.palette[px[y][x]]||'#000';
      g.fillRect(ox+x*z,oy+y*z,z,z);
    }
    if(c===sel){ g.strokeStyle='#d8a657'; g.lineWidth=1;
      g.strokeRect(ox+0.5,oy+0.5,cw*z-1,ch*z-1); }
  }
  s.onclick=e=>{
    const r=s.getBoundingClientRect();
    const cx=Math.floor((e.clientX-r.left)/(cw*z));
    const cy=Math.floor((e.clientY-r.top)/(ch*z));
    const i=cy*cols+cx;
    if(i>=0&&i<R.count){sel=i;undoStack=[];draw();}
  };
  drawCell();
}
function drawCell(){
  const cw=W(), ch=H();
  const z=Math.max(4,Math.floor(Math.min(480/cw,480/ch)));
  const c=$('edit'); c.width=cw*z; c.height=ch*z;
  const g=c.getContext('2d'); g.imageSmoothingEnabled=false;
  const px=preview||R.cells[sel];
  for(let y=0;y<ch;y++)for(let x=0;x<cw;x++){
    g.fillStyle=R.palette[px[y][x]]||'#000'; g.fillRect(x*z,y*z,z,z);
  }
  g.strokeStyle='rgba(255,255,255,.07)';
  for(let x=0;x<=cw;x++){g.beginPath();g.moveTo(x*z,0);g.lineTo(x*z,ch*z);g.stroke();}
  for(let y=0;y<=ch;y++){g.beginPath();g.moveTo(0,y*z);g.lineTo(cw*z,y*z);g.stroke();}
  $('cellno').textContent=sel+'  ($'+(R.base+sel*R.width).toString(16).toUpperCase()+')';
  const at=e=>{const r=c.getBoundingClientRect();
    return [Math.floor((e.clientX-r.left)/z),Math.floor((e.clientY-r.top)/z)];};
  c.onmousedown=e=>{painting=true; start=at(e); onDown(start[0],start[1]);};
  c.onmousemove=e=>{if(painting){const p=at(e); onMove(p[0],p[1]);}};
  window.onmouseup=()=>{if(painting){painting=false; onUp();}};
}

/* Every tool builds a whole cell and posts it once. Painting pixel by pixel
   over HTTP was fine for a pen and hopeless for a flood fill. */
function inb(x,y){return x>=0&&y>=0&&x<W()&&y<H();}
function plot(g,x,y){ if(inb(x,y)) g[y][x]=colour; }

function lineTo(g,x0,y0,x1,y1){
  let dx=Math.abs(x1-x0), dy=-Math.abs(y1-y0);
  let sx=x0<x1?1:-1, sy=y0<y1?1:-1, err=dx+dy;
  for(;;){
    plot(g,x0,y0);
    if(x0===x1&&y0===y1) break;
    const e2=2*err;
    if(e2>=dy){err+=dy;x0+=sx;}
    if(e2<=dx){err+=dx;y0+=sy;}
  }
}
function rectTo(g,x0,y0,x1,y1,fill){
  const a=Math.min(x0,x1), b=Math.max(x0,x1);
  const c=Math.min(y0,y1), d=Math.max(y0,y1);
  for(let y=c;y<=d;y++)for(let x=a;x<=b;x++)
    if(fill||y===c||y===d||x===a||x===b) plot(g,x,y);
}
function inside(x,y,cx,cy,rx,ry){
  return ((x-cx)/rx)*((x-cx)/rx)+((y-cy)/ry)*((y-cy)/ry) <= 1;
}
function ellipseTo(g,x0,y0,x1,y1,fill){
  const cx=(x0+x1)/2, cy=(y0+y1)/2;
  const rx=Math.max(0.5,Math.abs(x1-x0)/2), ry=Math.max(0.5,Math.abs(y1-y0)/2);
  for(let y=Math.floor(cy-ry);y<=Math.ceil(cy+ry);y++){
    for(let x=Math.floor(cx-rx);x<=Math.ceil(cx+rx);x++){
      if(!inside(x,y,cx,cy,rx,ry)) continue;
      if(fill){ plot(g,x,y); continue; }
      /* an outline pixel is one with a neighbour outside the ellipse */
      const edge = !inside(x+1,y,cx,cy,rx,ry) || !inside(x-1,y,cx,cy,rx,ry)
                || !inside(x,y+1,cx,cy,rx,ry) || !inside(x,y-1,cx,cy,rx,ry);
      if(edge) plot(g,x,y);
    }
  }
}
function flood(g,x,y){
  if(!inb(x,y)) return;
  const want=g[y][x];
  if(want===colour) return;
  const q=[[x,y]];
  while(q.length){
    const p=q.pop(), px=p[0], py=p[1];
    if(!inb(px,py)||g[py][px]!==want) continue;
    g[py][px]=colour;
    q.push([px+1,py],[px-1,py],[px,py+1],[px,py-1]);
  }
}

function onDown(x,y){
  if(tool==='pen'){ pushUndo(); plot(R.cells[sel],x,y); drawCell(); return; }
  if(tool==='fill'){ pushUndo(); flood(R.cells[sel],x,y); commit(); return; }
  preview=copyGrid(R.cells[sel]); drawCell();
}
function onMove(x,y){
  if(tool==='pen'){
    const g=R.cells[sel];
    if(inb(x,y)&&g[y][x]!==colour){ plot(g,x,y); drawCell(); }
    return;
  }
  if(tool==='fill') return;
  preview=copyGrid(R.cells[sel]);
  const x0=start[0], y0=start[1];
  if(tool==='line') lineTo(preview,x0,y0,x,y);
  else if(tool==='rect') rectTo(preview,x0,y0,x,y,false);
  else if(tool==='frect') rectTo(preview,x0,y0,x,y,true);
  else if(tool==='ellipse') ellipseTo(preview,x0,y0,x,y,false);
  else ellipseTo(preview,x0,y0,x,y,true);
  drawCell();
}
function onUp(){
  if(tool==='pen'){ commit(); return; }
  if(tool==='fill') return;
  if(preview){ pushUndo(); R.cells[sel]=preview; preview=null; commit(); }
}

function pushUndo(){
  undoStack.push(copyGrid(R.cells[sel]));
  if(undoStack.length>64) undoStack.shift();
}
async function commit(){
  const r=await fetch('/api/cell',{method:'POST',
    body:JSON.stringify({cell:sel,rows:R.cells[sel]})});
  const j=await r.json();
  if(j.error){ msg(j.error,true); return; }
  if(j.cell) R.cells[sel]=j.cell;
  preview=null; draw();
}
function undo(){
  if(!undoStack.length){ msg('nothing to undo'); return; }
  R.cells[sel]=undoStack.pop(); commit();
}
function copyCell(){ clip=copyGrid(R.cells[sel]); msg('cell '+sel+' copied'); }
function pasteCell(){
  if(!clip){ msg('nothing copied'); return; }
  if(clip.length!==H()||clip[0].length!==W()){
    msg('that cell was '+clip[0].length+'x'+clip.length+', this one is '
        +W()+'x'+H(),true);
    return;
  }
  pushUndo(); R.cells[sel]=copyGrid(clip); commit();
}
function clearCell(){
  pushUndo();
  R.cells[sel]=Array.from({length:H()},()=>new Array(W()).fill(0));
  commit();
}
function flip(dir){
  pushUndo();
  const g=R.cells[sel];
  R.cells[sel]= dir==='h' ? g.map(r=>r.slice().reverse()) : g.slice().reverse();
  commit();
}
function shift(dx,dy){
  pushUndo();
  const g=R.cells[sel], w=W(), h=H();
  const out=Array.from({length:h},()=>new Array(w).fill(0));
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    out[(y+dy+h)%h][(x+dx+w)%w]=g[y][x];
  }
  R.cells[sel]=out; commit();
}

window.addEventListener('keydown',e=>{
  if(!R) return;
  if(e.ctrlKey&&e.key==='z'){e.preventDefault();undo();}
  else if(e.ctrlKey&&e.key==='c'){e.preventDefault();copyCell();}
  else if(e.ctrlKey&&e.key==='v'){e.preventDefault();pasteCell();}
  else if(e.key>='0'&&e.key<='3'&&Number(e.key)<R.colours){colour=Number(e.key);pal();}
  else if(e.key==='['&&sel>0){sel--;undoStack=[];draw();}
  else if(e.key===']'&&sel<R.count-1){sel++;undoStack=[];draw();}
});

async function go(){
  const body={width:+$('w').value,height:+$('h').value,count:+$('n').value,
    stride:+$('stride').value,mode:$('mode').value};
  const t=$('base').value.trim();
  body.base = t.startsWith('0x')||t.startsWith('$')
    ? parseInt(t.replace('$','0x'),16) : parseInt(t,16);
  const r=await fetch('/api/goto',{method:'POST',body:JSON.stringify(body)});
  const j=await r.json();
  if(j.error){msg(j.error,true);return;}
  R=j; sel=0; undoStack=[]; clip=null; draw(); msg('');
}
async function save(){
  const r=await fetch('/api/save',{method:'POST',body:'{}'});
  const j=await r.json();
  if(j.error) msg(j.error,true);
  else msg('wrote '+j.path+' — '+j.changed+' bytes changed, all inside the '
           +j.owned+' this region owns');
}
function msg(s,bad){const m=$('msg');m.textContent=s;m.className=bad?'err':'muted';}

load();
</script>
"""


def from_manifest(path, cart):
    """Pick the first certain graphics entry out of an assets.py manifest."""
    doc = json.load(open(path, encoding="utf-8"))
    for g in doc.get("graphics", []):
        if not g.get("certain"):
            continue
        sp, addr = g["loc"].split(":")
        return sp, int(addr, 16), g.get("width") or 1
    raise ValueError(
        "no confidently-placed graphics in %s. Every entry there is in the "
        "paged window with no recorded bank, so pick one by hand with --space "
        "and --base." % os.path.basename(path))


def main():
    global CART, DATA, REGION, PATH, PALETTE, CANDIDATES
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--manifest", help="an assets.py manifest to open")
    ap.add_argument("--space")
    ap.add_argument("--base", type=lambda v: int(v, 0))
    ap.add_argument("--width", type=int, default=1,
                    help="bytes per cell per line (1 for a character set)")
    ap.add_argument("--height", type=int, default=8, help="lines per cell")
    ap.add_argument("--count", type=int, default=256, help="how many cells")
    ap.add_argument("--stride", type=int, default=256,
                    help="bytes between one line and the next (256 for the "
                         "line-planar layout MARIA fetches)")
    ap.add_argument("--mode", choices=sorted(MODES), default="160",
                    help="160 = 2 bits per pixel (160A/B); 320 = 1 bit "
                         "(320A/D only -- 320B/C pair bytes and are not "
                         "modelled)")
    ap.add_argument("--palette", help="three hex colour bytes, e.g. 36,13,0D")
    ap.add_argument("--palette-from", metavar="MANIFEST",
                    help="an assets.py manifest, to offer the palettes the "
                         "cartridge's own code writes")
    ap.add_argument("--ascending", action="store_true",
                    help="read lines in ascending page order. MARIA counts a "
                         "zone offset DOWN, so descending is right for sprites "
                         "and character sets; use this for data that is not a "
                         "zone at all.")
    ap.add_argument("--side", choices=["sally", "maria"], default="sally",
                    help="bankset cartridges only: which of the two parallel "
                         "sets to edit. The artwork is usually the MARIA one, "
                         "because that is the half the CPU never reads.")
    ap.add_argument("--port", type=int, default=8124)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        cart = cart_module.Cart(args.rom, side=args.side)
    except (cart_module.UnknownMapper, cart_module.UnknownSpace, IOError) as e:
        sys.stderr.write("%s\n" % e)
        return 2

    space, base, width = args.space, args.base, args.width
    if args.manifest and not (space and base is not None):
        try:
            space, base, width = from_manifest(args.manifest, cart)
        except (ValueError, IOError) as e:
            sys.stderr.write("%s\n" % e)
            return 2
    if not space or base is None:
        sys.stderr.write("give --space and --base, or a --manifest to read "
                         "them from.\n")
        return 2
    if space not in cart.spaces():
        sys.stderr.write("no space %r in this cartridge; it has %s\n"
                         % (space, ", ".join(cart.spaces())))
        return 2

    if args.palette:
        cols = [int(x, 16) for x in args.palette.replace("$", "").split(",")]
        set_palette([None] + cols[:3])
    if args.palette_from:
        try:
            doc = json.load(io.open(args.palette_from, encoding="utf-8"))
            CANDIDATES = doc.get("palettes", [])
        except (ValueError, IOError) as e:
            sys.stderr.write("could not read %s: %s\n"
                             % (args.palette_from, e))

    CART = cart
    PATH = os.path.abspath(args.rom)
    DATA = bytearray(open(PATH, "rb").read())
    REGION = Region(cart, space, base, width, args.height, args.count,
                    args.stride, args.mode, descending=not args.ascending)

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("%s%s  %s:$%04X  %d cells of %dx%d, stride %d, mode %s"
          % (os.path.basename(PATH),
             ("  [%s side]" % args.side) if cart.bankset else "",
             space, base, args.count,
             width * MODES[args.mode][1], args.height, args.stride, args.mode))
    if cart.bankset and args.side == "sally":
        print("  this is a bankset cartridge and you are editing the CPU's "
              "half.")
        print("  Its artwork is normally in the other one: --side maria")
    print("open %s" % url)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped%s" % (" -- unsaved changes" if DIRTY else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
