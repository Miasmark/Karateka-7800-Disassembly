"""Build a cartridge that samples POKEY's RANDOM register at known cycle spacing.

Written to pin down POKEY's polynomial counter by measurement rather than by
trusting a reference implementation, because sim.py needs RANDOM to be right
before a cartridge that GENERATES its music from it can be scored.

What it established, and what it did not:

  * The underlying recurrence is consistent with the classic 17-bit
    polynomial, s[n] = s[n-12] ^ s[n-17], clocked one bit per CPU cycle.
    Across every tap pair from 1..17 and rates of 1/4, 1/2, 1 and 2 bits per
    cycle, that pairing at rate 1 scored 95 consistent against 19 violations
    and nothing else came close.
  * RANDOM is NOT eight consecutive bits of that register. An exhaustive
    search over all 131,071 phases found no contiguous window reproducing the
    measured bytes, in either bit order, inverted or not. Nor does a model
    where each of the eight byte bits is a fixed tap of a plain shift
    register: that search also came back empty.

So the recurrence is measured and the output mapping is not. Anyone
continuing should probably stop black-boxing it and read how the emulator
derives the register.


The samples land in RAM at $1800.. and are read out with MAME's debugger,
which can read memory where taps cannot be trusted. Spacing is fixed by the
loop's own cycle count, so the relative timing of every sample is exact even
though the absolute phase at the first read is unknown.
"""
import sys, struct, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "tools"))
import asm

def build(path, pad_nops=0, audctl=0x00):
    L=[]; a=L.append
    a("    .org $C000")
    a("reset:")
    a("    SEI"); a("    CLD"); a("    LDX #$FF"); a("    TXS")
    a("    LDA #$17"); a("    STA $01")
    a("    LDA #$00"); a("    STA $38"); a("    STA $01")
    a("    LDA #$60"); a("    STA $3C")          # MARIA DMA off: no DMA theft
    a("    LDA #$03"); a("    STA $400F")        # SKCTL: POKEY out of reset
    a("    LDA #$%02X" % audctl); a("    STA $4008")
    a("    LDX #$00")
    a("sample:")
    a("    LDA $400A")                            # 4
    a("    STA $1800,X")                          # 5
    for _ in range(pad_nops):
        a("    NOP")                              # 2 each
    a("    INX")                                  # 2
    a("    BNE sample")                           # 3 taken / 2 fall through
    a("    LDA #$5A"); a("    STA $1900")         # done marker
    a("spin:"); a("    JMP spin")
    a("pad:")
    a("    .res $FFFA-pad,$00")
    a("    .word reset"); a("    .word reset"); a("    .word reset")
    data=asm.Assembler().assemble("\n".join(L).splitlines())
    assert len(data)==0x4000, len(data)
    h=bytearray(128); h[0]=1; h[1:10]=b"ATARI7800"
    h[17:28]=b"POLY ORACLE"; h[49:53]=struct.pack(">I",len(data))
    h[53:55]=struct.pack(">H",0x0001)   # POKEY @ $4000
    h[55]=1; h[56]=1; h[57]=0
    h[100:128]=b"ACTUAL CART DATA STARTS HERE"
    open(path,"wb").write(bytes(h)+data)
    return 14+2*pad_nops   # cycles per sample

if __name__=="__main__":
    for n in (0,1,3):
        c=build("oracle%d.a78"%n, pad_nops=n)
        print("oracle%d.a78: %d cycles between samples"%(n,c))
