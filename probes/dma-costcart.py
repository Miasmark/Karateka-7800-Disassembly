"""Build a 7800 cartridge that measures how many CPU cycles survive DMA.

Each frame it zeroes a counter, spins a fixed-cost loop for the whole visible
period, and publishes the count when VBLANK starts. More drawing -> fewer
iterations. Everything about the ROM is identical between runs except the
display lists, so the difference IS the DMA cost.

This is the instrument behind the cost table in docs/hardware.md and the model
in tools/dmabudget.py. It is kept so the numbers can be re-derived rather than
taken on trust -- and re-derived on other emulators, or on hardware.

    python probes/dma-costcart.py                 # writes t.a78, 1 object of 8 bytes
    mame a7800 -cart t.a78 -autoboot_script probes/dma-count.lua \
         -window -seconds_to_run 12 -nothrottle -sound none -video soft

dma-count.lua prints the per-frame iteration count. Multiply by the loop cost
(14.0156 cycles, calibrated with the `nops` parameter) to get cycles, and
compare against a run with dma_on=False for the ceiling.

build() takes the display-list shape: objects per zone, object width, how many
zones carry them, zone height, 4- or 5-byte entries, and character mode with
one or two bytes per character.
"""
import os, sys, struct

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "tools"))
import asm

ZONE_LINES = 16
ZONES      = 12          # 12 * 16 = 192 visible scanlines
GFX        = 0xD000
DLL        = 0xE000
DLS        = 0xE100
DL_STRIDE  = 64

def build(path, nobj, width, zones_used=ZONES, dma_on=True, five_byte=False, nops=0, zlines=ZONE_LINES, nzones=ZONES, indirect=False, charwidth1=False):
    L=[]; a=L.append
    a("MSTAT = $28"); a("DPPH = $2C"); a("DPPL = $30"); a("CTRL = $3C")
    a("OFFSET = $38"); a("BACKGRND = $20"); a("P0C1 = $21"); a("P0C2 = $22")
    a("P0C3 = $23"); a("INPTCTRL = $01")
    a("cnt_lo = $80"); a("cnt_hi = $81"); a("res_lo = $90"); a("res_hi = $91")
    a("    .org $C000")
    a("reset:")
    a("    SEI"); a("    CLD"); a("    LDX #$FF"); a("    TXS")
    a("    LDA #$17"); a("    STA INPTCTRL")
    a("    LDA #$00"); a("    STA OFFSET"); a("    STA INPTCTRL")
    a("    LDA #$60"); a("    STA CTRL")          # DMA off while we set up
    a("    LDA #$00"); a("    STA BACKGRND")
    a("    LDA #$0F"); a("    STA P0C1")
    a("    STA P0C2"); a("    STA P0C3")
    a("    LDA #$00"); a("    STA res_lo"); a("    STA res_hi")
    a("    LDA #>dll"); a("    STA DPPH")
    a("    LDA #<dll"); a("    STA DPPL")
    a("    LDA #$D0"); a("    STA $34")   # CHARBASE
    a("    LDA #$%02X" % ((0x40|(0x10 if charwidth1 else 0)) if dma_on else 0x60)); a("    STA CTRL")
    a("main:")
    a("wait_vb:")                                  # wait until NOT in vblank
    a("    LDA MSTAT"); a("    BMI wait_vb")
    a("    LDA #$00"); a("    STA cnt_lo"); a("    STA cnt_hi")
    a("count:")                                    # 14 cycles/iteration
    a("    INC cnt_lo")
    a("    BNE no_carry")
    a("    INC cnt_hi")
    a("no_carry:")
    for _ in range(nops):
        a("    NOP")
    a("    LDA MSTAT")
    a("    BPL count")                             # spin for the visible period
    a("    LDA cnt_lo"); a("    STA res_lo")
    a("    LDA cnt_hi"); a("    STA res_hi")
    a("    JMP main")
    a("code_end:")
    a("    .res $D000-code_end,$00")
    a("gfx:")
    a("    .res $1000,$FF")                        # $D000-$DFFF, all pixels lit
    a("dll:")
    for z in range(nzones):
        dl = DLS + z*DL_STRIDE
        a("    .byte $%02X,$%02X,$%02X" % (zlines-1, dl>>8, dl&0xFF))
    a("dll_end:")
    a("    .res $E100-dll_end,$00")
    a("dls:")
    for z in range(nzones):
        row=[]
        if z < zones_used:
            for i in range(nobj):
                if indirect:
                    # 5-byte with bit 5 set: the address names a CHARACTER LIST,
                    # and each character is fetched from CHARBASE separately.
                    row += [0x00, 0x60, 0xEF, ((32-width)&0x1F), (i*width)%160]
                elif five_byte:
                    # 5-byte: byte1 has low 5 bits zero (write mode / indirect)
                    row += [0x00, 0x40, GFX>>8, ((32-width)&0x1F), (i*width)%160]
                else:
                    row += [0x00, ((32-width)&0x1F), GFX>>8, (i*width)%160]
        row += [0x00,0x00]
        row += [0x00]*(DL_STRIDE-len(row))
        a("    .byte " + ",".join("$%02X"%b for b in row))
    a("dls_pad:")
    a("    .res $EF00-dls_pad,$00")
    a("charlist:")
    a("    .res $100,$41")
    a("dls_end:")
    a("    .res $FFFA-dls_end,$00")
    a("    .word reset"); a("    .word reset"); a("    .word reset")
    src="\n".join(L)+"\n"
    data=asm.Assembler().assemble(src.splitlines())
    assert len(data)==0x4000, len(data)
    h=bytearray(128); h[0]=1; h[1:10]=b"ATARI7800"
    h[17:26]=b"DMA TEST "; h[49:53]=struct.pack(">I",len(data))
    h[53:55]=struct.pack(">H",0); h[55]=1; h[56]=1; h[57]=0
    h[100:128]=b"ACTUAL CART DATA STARTS HERE"
    open(path,"wb").write(bytes(h)+data)

if __name__=="__main__":
    build("t.a78", 1, 8)
    print("ok")
