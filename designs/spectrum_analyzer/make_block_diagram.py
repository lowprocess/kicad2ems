#!/usr/bin/env python3
"""
Draw the analyzer's block diagram: block_diagram.svg, beside this file.

    python make_block_diagram.py            the standalone SVG (light and dark)
    python make_block_diagram.py --inline   the bare <svg> on stdout, styled by
                                            the page it is put in (the classes
                                            and colour variables of STYLE)

The drawing is the design that sasim.analyzer builds: change both together.
"""

import os
import sys

W, H = 1180, 770
YA, YB1, YB2, YIN, YB, YM2, YIF = 150, 290, 390, 270, 340, 245, 590

out = []


def add(s):
    out.append(s)


def text(x, y, s, cls, anchor="middle", extra=""):
    add('<text x="%g" y="%g" class="%s" text-anchor="%s"%s>%s</text>'
        % (x, y, cls, anchor, extra, s))


def block(x, y, w, lines, printed=False, h=52):
    """A box centred on the row y, its left edge at x; lines: the name, then
    up to two details."""
    add('<rect x="%g" y="%g" width="%g" height="%g" rx="4" class="blk%s"/>'
        % (x, y - h / 2, w, h, " printed" if printed else ""))
    n = len(lines)
    y0 = y - (n - 1) * 6.5 + 4
    for i, s in enumerate(lines):
        text(x + w / 2, y0 + i * 13, s, "t1" if i == 0 else "t2")
    return x + w


def pad(x, y, w=34):
    add('<rect x="%g" y="%g" width="%g" height="34" rx="4" class="blk"/>'
        % (x, y - 17, w))
    text(x + w / 2, y + 4, "3 dB", "t2")
    return x + w


def switch(x, y0, y1, label, w=40):
    add('<rect x="%g" y="%g" width="%g" height="%g" rx="4" class="blk"/>'
        % (x, y0, w, y1 - y0))
    cx, cy = x + w / 2, (y0 + y1) / 2
    text(cx, cy + 4, label, "t1", extra=' transform="rotate(-90 %g %g)"'
         % (cx, cy))
    return x + w


def mixer(cx, cy, r=17):
    add('<circle cx="%g" cy="%g" r="%g" class="mix"/>' % (cx, cy, r))
    d = r * 0.6
    add('<path d="M%g %g L%g %g M%g %g L%g %g" class="mixx"/>'
        % (cx - d, cy - d, cx + d, cy + d, cx - d, cy + d, cx + d, cy - d))


def wire(points, cls="w", arrow=True):
    """A polyline through the points, with an arrowhead at its end."""
    (x0, y0), (x1, y1) = points[-2], points[-1]
    dx, dy = x1 - x0, y1 - y0
    n = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / n, dy / n
    end = (x1 - ux * 6, y1 - uy * 6) if arrow else (x1, y1)
    pts = points[:-1] + [end]
    add('<polyline points="%s" class="%s"/>'
        % (" ".join("%g,%g" % p for p in pts), cls))
    if arrow:
        px, py = -uy, ux
        add('<polygon points="%g,%g %g,%g %g,%g" class="h%s"/>'
            % (x1, y1, x1 - ux * 8 + px * 3.6, y1 - uy * 8 + py * 3.6,
               x1 - ux * 8 - px * 3.6, y1 - uy * 8 - py * 3.6, cls))


def draw():
    text(24, 30, "RF FRONT END", "sec", "start")

    # ---- the common input
    add('<circle cx="34" cy="%g" r="5" class="mix"/>' % YIN)
    text(34, YIN - 16, "RF in", "t1")
    text(18, YIN + 44, "10 MHz to", "t2", "start")
    text(18, YIN + 57, "5.49 GHz", "t2", "start")
    wire([(39, YIN), (64, YIN)])
    x = block(64, YIN, 80, ["DC block", "+ limiter"])
    wire([(x, YIN), (x + 18, YIN)])
    x = block(x + 18, YIN, 80, ["Attenuator", "PE43711", "0–31.75 dB"])
    wire([(x, YIN), (x + 20, YIN)])
    x = switch(x + 20, 122, 418, "SW1 · PE42540 SP4T · band select")
    sw1 = x                                      # 302

    # ---- band A
    xa = sw1 + 26
    text(sw1 + 8, YA - 36, "Band A · 10 MHz–2.29 GHz", "lbl", "start")
    wire([(sw1, YA), (xa, YA)], "wa")
    x = block(xa, YA, 96, ["Low-pass", "2× LFCN-2250+", "2.3 GHz"])
    wire([(x, YA), (x + 16, YA)], "wa")
    x = pad(x + 16, YA)
    mx1 = x + 16 + 17
    wire([(x, YA), (mx1 - 17, YA)], "wa")
    mixer(mx1, YA)
    text(mx1, YA + 36, "MIX1 · SIM-83+", "t1")
    text(mx1, YA + 49, "in: IF port, out: RF port", "t2")
    x = mx1 + 17
    wire([(x, YA), (x + 16, YA)], "wa")
    x = pad(x + 16, YA)
    wire([(x, YA), (x + 18, YA)], "wa")
    text(x + 18, YA - 36, "first IF 3.21 GHz", "lbl", "start")
    x = block(x + 18, YA, 84, ["Band-pass", "hairpin, N=5", "3.21 GHz"],
              printed=True)
    wire([(x, YA), (x + 18, YA)], "wa")
    x = block(x + 18, YA, 84, ["IF1 amp", "PMA3-83LN+", "+21 dB"])
    wire([(x, YA), (x + 18, YA)], "wa")
    x = block(x + 18, YA, 84, ["Low-pass", "radial stub", "4.0 GHz"],
              printed=True)
    sw2 = 972
    wire([(x, YA), (sw2, YA)], "wa")

    # LO1, above the first mixer
    add('<rect x="%g" y="34" width="150" height="46" rx="4" class="blk"/>'
        % (mx1 - 75))
    text(mx1, 53, "LO1 · LMX2582", "t1")
    text(mx1, 67, "f + 3.21 GHz: 3.21–5.50 GHz", "t2")
    wire([(mx1, 80), (mx1, YA - 17)], "wl")

    # ---- band B
    xb = sw1 + 26
    for y, name, bpf, lpf in (
            (YB1, "Band B1 · 2.29–3.45 GHz", "2.3–3.5 GHz",
             "4.0 GHz"),
            (YB2, "Band B2 · 3.45–5.49 GHz", "3.4–5.5 GHz",
             "6.4 GHz")):
        text(sw1 + 8, y - 34, name, "lbl", "start")
        wire([(sw1, y), (xb, y)], "wb")
        x = block(xb, y, 84, ["Band-pass", "stub, N=5", bpf], printed=True)
        wire([(x, y), (x + 18, y)], "wb")
        x = block(x + 18, y, 84, ["Low-pass", "radial stub", lpf],
                  printed=True)
        wire([(x, y), (x + 26, y)], "wb")
    x = switch(x + 26, YB1 - 30, YB2 + 30, "SW3 · PE42540")
    wire([(x, YB), (x + 30, YB)], "wb")
    x = block(x + 30, YB, 104, ["Preamp", "PMA3-83LN+", "+21 dB, or bypass"])
    wire([(x, YB), (sw2, YB)], "wb")

    # ---- the second mixer
    x = switch(sw2, 122, 368, "SW2 · PE42540 · mixer 2 select")
    mx2 = 1090
    wire([(x, YM2), (mx2 - 17, YM2)])
    mixer(mx2, YM2)
    text(mx2 + 24, YM2 - 3, "MIX2", "t1", "start")
    text(mx2 + 24, YM2 + 10, "SIM-83+", "t2", "start")
    add('<rect x="%g" y="118" width="140" height="60" rx="4" class="blk"/>'
        % (mx2 - 70))
    text(mx2, 137, "LO2 · LMX2582", "t1")
    text(mx2, 151, "A: 3.20 or 3.21 GHz", "t2")
    text(mx2, 164, "B: f ± 10 MHz", "t2")
    wire([(mx2, 178), (mx2, YM2 - 17)], "wl")

    # ---- down to the IF row
    yr, x0 = 476, 40
    wire([(mx2, YM2 + 17), (mx2, yr), (x0, yr), (x0, YIF), (74, YIF)])
    text(mx2 - 12, yr - 8, "second IF · 10 MHz centre, 5–15 MHz "
         "kept", "lbl", "end")
    text(58, 530, "IF AND DIGITISER", "sec", "start")
    x, w, g = 74, 98, 24
    for i, lines in enumerate((
            ["Diplexer", "low-pass 30 MHz", "HP arm to 50 Ω"],
            ["IF amp", "LMH6629", "+20 dB"],
            ["Attenuator", "PE43711", "0–31.75 dB"],
            ["Anti-alias LPF", "elliptic, N=7", "16.5 MHz"],
            ["IF amp", "LMH6629", "+20 dB"],
            ["ADC driver", "THS4541", "+6 dB, differential"],
            ["ADC", "12 bit", "40 MSPS"],
            ["RP2350", "PIO capture", "FFT, control"],
            ["PC", "sweep, stitching", "display"])):
        if i:
            wire([(x, YIF), (x + g, YIF)])
        if i == 6:
            adc = x + g + w / 2
        if i == 7:
            mcu = x + g + w / 2
            text(x + g / 2, YIF - 34, "12-bit parallel", "t2")
        if i == 8:
            text(x + g / 2, YIF - 34, "USB", "t2")
        x = block(x + (g if i else 0), YIF, w, lines)

    # ---- the clock
    yc = 690
    add('<rect x="%g" y="%g" width="98" height="40" rx="4" class="blk"/>'
        % (adc - 49, yc - 20))
    text(adc, yc - 2, "Oscillator", "t1")
    text(adc, yc + 11, "40 MHz", "t2")
    wire([(adc, yc - 20), (adc, YIF + 26)], "wc")
    wire([(adc + 49, yc), (mcu, yc), (mcu, YIF + 26)], "wc")
    text(adc - 62, yc - 2, "also the reference of LO1 and LO2", "t2", "end")
    text(adc - 62, yc + 11, "(÷4: a 10 MHz phase detector, integer-N)",
         "t2", "end")
    text(mcu + 14, yc - 2, "SPI: LO1, LO2, attenuators", "t2", "start")
    text(mcu + 14, yc + 11, "GPIO: SW1–SW3", "t2", "start")

    # ---- what a dashed outline means
    add('<rect x="24" y="738" width="26" height="16" rx="3" '
        'class="blk printed"/>')
    text(58, 750, "printed on the board (0.508 mm RO4003C), from dfsynth "
         "and mom25d", "t2", "start")


# the colours and type of the drawing; a page that takes the --inline form
# defines the same variables and classes
STYLE = """
svg { --bg: #f6f7f9; --surface: #ffffff; --fg: #17202b; --muted: #566273;
      --line: #b9c2ce; --a: #1d6fd6; --b: #c2571a;
      font-family: system-ui, -apple-system, "Segoe UI", Helvetica, Arial,
                   sans-serif; }
@media (prefers-color-scheme: dark) {
  svg { --bg: #12161c; --surface: #1b222b; --fg: #e6ebf1; --muted: #9aa6b4;
        --line: #465263; --a: #5aa2f5; --b: #f0914d; } }
.ground { fill: var(--bg); }
""" + """
.blk { fill: var(--surface); stroke: var(--line); stroke-width: 1.2; }
.blk.printed { stroke: var(--fg); stroke-dasharray: 4 3; }
.mix { fill: var(--surface); stroke: var(--fg); stroke-width: 1.4; }
.mixx { stroke: var(--fg); stroke-width: 1.4; fill: none; }
.t1 { fill: var(--fg); font-size: 11.5px; font-weight: 600; }
.t2 { fill: var(--muted); font-size: 10.5px; }
.lbl { fill: var(--fg); font-size: 12px; font-weight: 600; }
.sec { fill: var(--muted); font-size: 10.5px; font-weight: 600;
       letter-spacing: 0.09em; }
.w, .wa, .wb, .wl, .wc { fill: none; stroke-width: 1.7;
                         stroke-linejoin: round; }
.w { stroke: var(--fg); } .hw { fill: var(--fg); }
.wa { stroke: var(--a); } .hwa { fill: var(--a); }
.wb { stroke: var(--b); } .hwb { fill: var(--b); }
.wl { stroke: var(--muted); stroke-width: 1.3; } .hwl { fill: var(--muted); }
.wc { stroke: var(--muted); stroke-width: 1.3; stroke-dasharray: 5 4; }
.hwc { fill: var(--muted); }
"""
LABEL = ("Block diagram: RF in, a step attenuator and a band switch; band A "
         "up-converts in the first mixer to a 3.21 GHz first IF and comes "
         "down to 10 MHz in the second; bands B1 and B2 go through a "
         "preselector straight into the second mixer; the 10 MHz IF is "
         "amplified, filtered and sampled at 40 MSPS, and an RP2350 sends "
         "the captures to a PC.")


def svg(inline):
    del out[:]
    draw()
    head = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'role="img" aria-label="%s">' % (W, H, LABEL))
    if inline:
        return head + "\n" + "\n".join(out) + "\n</svg>\n"
    return (head + "\n<style>%s</style>\n" % STYLE
            + '<rect width="%d" height="%d" class="ground"/>\n' % (W, H)
            + "\n".join(out) + "\n</svg>\n")


if __name__ == "__main__":
    if "--inline" in sys.argv:
        sys.stdout.write(svg(True))
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "block_diagram.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg(False))
        print("wrote", path)
