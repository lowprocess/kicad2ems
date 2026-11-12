#!/usr/bin/env python3
"""
The line model on a real board, KiCad's CM5 Minima demo: build time and solve counts with the formulas, with
--lines-field off (field-solved coupled sections and grounded CPW only), by default (auto: also the single sections
whose cut holds what the formulas leave out) and with --lines-field (every section), the sections the field solver
moves most against the formulas, and how far each mode's single sections are from their cuts' values. The board is
not in the repository (CERN-OHL-S, KiCad's demos):

    curl -O https://gitlab.com/kicad/code/kicad/-/raw/master/demos/cm5_minima/CM5_MINIMA_3.kicad_pcb
    python tools/cm5_line_model.py CM5_MINIMA_3.kicad_pcb [--nets HDMI] [--modes formulas,off,default,field]
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from kicad2ems.board import Board                                        # noqa: E402
from kicad2ems.importers.kicad import KiCadBoard                         # noqa: E402
from kicad2ems.lines import builder, reference, topology                 # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board')
    ap.add_argument('--nets', help='only nets whose name contains this')
    ap.add_argument('--modes', default='formulas,off,default,field')
    a = ap.parse_args()
    m = Board(KiCadBoard(a.board), log=lambda *x: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))
    if a.nets:
        nets = [n for n in nets if a.nets in n]
    z0 = {}
    for mode in a.modes.split(','):
        reference.FIELD = {'field': 'all', 'default': 'auto'}.get(mode, 'off')
        reference.FIELD_CPW = topology.FIELD_GROUPS = mode != 'formulas'
        for f in (reference._solve_cut, reference._solve_cpwg, topology._group_c):
            f.cache_clear()
        t = time.time()
        lm = builder.build(m, nets=nets)
        failed = sum('field solver failed' in n for e in lm.elements for n in e.notes)
        print(f"{mode}: {len(nets)} nets, {time.time() - t:.1f} s; solves: {reference._solve_cut.cache_info().misses} "
              f"cuts, {reference._solve_cpwg.cache_info().misses} grounded CPW, "
              f"{topology._group_c.cache_info().misses} coupled sections; {failed} sections failed", flush=True)
        z0[mode] = [(e.z0, e.length, e.group is None and e.cut is not None, e.source) for e in lm.elements]
        rows = []
        for e in lm.elements:
            note = next((x for x in e.notes if x.startswith('field solver: Z0')), None)
            if note:
                f = float(note.rsplit('formula ', 1)[1].rstrip(')'))
                if f:
                    rows.append((e.z0 / f - 1, e, f))
        for d, e, f in sorted(rows, key=lambda r: -abs(r[0]))[:8]:
            print(f"  {d * 100:+6.1f}% {e.net.split('/')[-1]:16s} {e.layer:6s} {e.xs:18s} w {e.w:.3f} "
                  f"formula {f:6.1f} field {e.z0:6.1f} (sheet {e.z0_sheet:.1f})")
    if 'field' in z0:                                            # each mode's single sections against their cuts
        ref = z0['field']
        for mode, got in z0.items():
            if mode == 'field' or len(got) != len(ref):
                continue
            bands, solved = [0.0, 0.0, 0.0], 0
            for (z, length, single, source), (zc, *_) in zip(got, ref):
                if single and z and zc:
                    d = abs(z / zc - 1)
                    bands[0 if d < 0.03 else 1 if d < 0.1 else 2] += length
                    solved += source == 'cut'
            print(f"{mode}: single sections against their cuts: {bands[0]:.0f} mm within 3 %, {bands[1]:.1f} mm 3-10 %, "
                  f"{bands[2]:.1f} mm over 10 % ({solved} of them solved on their cut)")


if __name__ == '__main__':
    main()
