"""
dfsynth: distributed-element microstrip filter synthesis -> KiCad layout (+ kicad2ems validation).

    dfsynth --type bandpass --topology coupled --f0 2.4G --bw 200M --order 5 --substrate RO4003C-0.508 \\
            -o out/bpf --validate mom,lines
    dfsynth --type lowpass --topology stepped --f0 1.5G --atten 40@3G --substrate FR4-1.6 -o out/lpf
    dfsynth --type bandstop --topology stub --f0 2.4G --bw 1G -o out/bsf --validate mom --em-tune 2
    dfsynth --list
"""
import argparse
import math
import re
import sys
from dataclasses import asdict

import numpy as np

from . import __version__
from .microstrip import Substrate, PRESETS
from .prototype import estimate_order, RESPONSES
from .topologies import FabViolation, violations, Spec, KINDS, build, prepare, VIA_DRILL, VIA_SIZE
from .calibrate import calibrate, targets, sweep, model_response, band, space_map, REFINE_SECONDS
from . import kicad, validate as val


def freq(s):
    """'2.4G', '2.4GHz', '500M', '2.4e9' -> Hz."""
    m = re.fullmatch(r'\s*([0-9.eE+-]+)\s*([kKmMgG]?)(?:[hH][zZ])?\s*', s)
    if not m:
        raise argparse.ArgumentTypeError(f"bad frequency {s!r}")
    return float(m.group(1)) * {'': 1, 'k': 1e3, 'm': 1e6, 'g': 1e9}[m.group(2).lower()]


def _fmt(f):
    return f"{f / 1e9:.4f} GHz"


def parser():
    p = argparse.ArgumentParser(prog='dfsynth', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--list', action='store_true', help='list filter types, topologies and substrate presets')
    p.add_argument('--version', action='version', version=__version__)
    g = p.add_argument_group('filter')
    g.add_argument('--type', choices=list(KINDS), help='filter type')
    g.add_argument('--topology', help='layout topology (see --list); default: the first for the type')
    g.add_argument('--f0', type=freq, help='centre frequency (bandpass / bandstop) or cut-off (lowpass / highpass)')
    g.add_argument('--bw', type=freq, default=0.0,
                   help='bandwidth (bandpass / bandstop: ripple band for Chebyshev, -3 dB for Butterworth); '
                        'highpass: pass-band width above the cut-off (default 2 x f0)')
    g.add_argument('--response', choices=RESPONSES, default='chebyshev')
    g.add_argument('--ripple', type=float, default=0.1, help='Chebyshev pass-band ripple, dB (default 0.1)')
    g.add_argument('--order', type=int, help='filter order (default: from --atten, else 5)')
    g.add_argument('--atten', help='stop-band spec DB@FREQ (e.g. 40@3G): picks the lowest order meeting it')
    g.add_argument('--z0', type=float, default=50.0, help='port impedance (default 50)')
    g.add_argument('--zhigh', type=float, default=0.0, help='lowpass: high-impedance line (ohm)')
    g.add_argument('--zlow', type=float, default=0.0, help='lowpass stepped: low-impedance section (ohm)')
    g.add_argument('--zstub', type=float, default=0.0, help='lowpass stub: stub impedance; interdigital: rod impedance (ohm)')
    g.add_argument('--stub-angle', type=float, default=60.0, help='lowpass radial: the fans\' apex angle (degrees, default 60)')
    g.add_argument('--hairpin-gap', type=float, default=0.0,
                   help="hairpin: gap between a resonator's own arms, mm (default: the wider of the strip width and "
                        "twice the largest coupling gap)")
    g.add_argument('--feed', type=float, default=0.0, help='mm of 50 ohm feed line at each port')
    s = p.add_argument_group('substrate')
    s.add_argument('--substrate', default='RO4003C-0.508', help='preset (see --list); default RO4003C-0.508')
    s.add_argument('--h', type=float, help='dielectric height, mm (overrides the preset)')
    s.add_argument('--er', type=float, help='relative permittivity (overrides the preset)')
    s.add_argument('--tand', type=float, help='loss tangent (overrides the preset)')
    s.add_argument('--copper', type=float, default=0.035, help='copper thickness, mm (default 0.035)')
    s.add_argument('--min-width', type=float, default=0.1,
                   help='fabrication minimum trace, mm (default 0.1, about the best PCB vendors do)')
    s.add_argument('--min-gap', type=float, default=0.1, help='fabrication minimum gap, mm (default 0.1)')
    s.add_argument('--allow-fab-violations', action='store_true',
                   help='write designs with traces or gaps below --min-width / --min-gap (refused by default)')
    o = p.add_argument_group('output and validation')
    o.add_argument('-o', '--out', default='filter', help='output base path: <out>.kicad_pcb, <out>.toml, ...')
    o.add_argument('--no-calibrate', action='store_true', help='skip the line-model calibration')
    o.add_argument('--refine-time', type=float, default=REFINE_SECONDS, metavar='S',
                   help=f'time limit of the calibration\'s final refinement (low-pass, band-stop, interdigital), '
                        f'seconds; past it the best trims so far are kept (default {REFINE_SECONDS:g})')
    o.add_argument('--validate', metavar='SOLVERS', help='run kicad2ems with these solvers (e.g. mom,lines)')
    o.add_argument('--em-tune', type=int, default=0, metavar='N',
                   help='with --validate: N rounds of correcting the design from the full-wave result (centre / '
                        'bandwidth; interdigital: every gap, tap and rod, by space mapping)')
    o.add_argument('--k2e-args', default='', help='extra kicad2ems arguments, quoted (e.g. "--mom-dmax 0.5")')
    return p


def list_all():
    print("filter types and topologies:")
    for k, ts in KINDS.items():
        print(f"  {k:9s} {', '.join(ts)}")
    print("\nsubstrate presets (h mm, er, tan d):")
    for name, (h, er, td, _) in PRESETS.items():
        print(f"  {name:16s} {h:6.3f}  {er:5.2f}  {td:.4f}")


def make_substrate(a):
    sub = Substrate.preset(a.substrate, t=a.copper, min_width=a.min_width, min_gap=a.min_gap)
    kw = {k: v for k, v in (('h', a.h), ('er', a.er), ('tand', a.tand)) if v is not None}
    if kw:
        sub = sub.with_(name=sub.name + ' (modified)', **kw)
    return sub


def order_from_atten(spec, atten):
    db_, fs = atten.split('@')
    a, fs = float(db_), freq(fs)
    kind, f0, fbw = band(spec)
    if spec.kind == 'lowpass':
        om = fs / spec.f0
    elif spec.kind == 'highpass':
        om = spec.f0 / fs
    elif spec.kind == 'bandpass':
        om = abs(fs / f0 - f0 / fs) / fbw
    else:
        om = fbw / abs(fs / f0 - f0 / fs)
    return estimate_order(spec.response, a, om, spec.ripple)


def describe(design, log=print):
    sp, sub = design.spec, design.sub
    log(f"\n{sp.kind} / {sp.topology}: {sp.response}"
        f"{f' {sp.ripple} dB' if sp.response == 'chebyshev' else ''}, order {sp.order}, "
        f"{'f0' if sp.kind in ('bandpass', 'bandstop') else 'fc'} {_fmt(sp.f0)}"
        + (f", bandwidth {sp.bw / 1e6:.1f} MHz" if sp.bw else ''))
    log(f"substrate {sub.name}: h {sub.h} mm, er {sub.er}, tan d {sub.tand}, copper {sub.t * 1e3:.0f} um")
    log("g = " + ', '.join(f'{x:.4f}' for x in design.g))
    log(f"calibration: kf {design.kf:.4f}, kb {design.kb:.4f}")
    log(f"\n  {'element':28s} {'Z (ohm)':>12s} {'width':>8s} {'length':>8s} {'gap':>8s}   (mm)")
    for name, z, w, l, s in design.rows:
        zs = f'{z:.1f}' if isinstance(z, (int, float)) else (z or '')
        f3 = lambda v: f'{v:8.3f}' if v is not None else ' ' * 8
        log(f"  {name:28s} {zs:>12s} {f3(w)} {f3(l)} {f3(s)}")
    log(f"\nboard {design.geometry.W:.2f} x {design.geometry.H:.2f} mm")
    for n in design.notes:
        log(f"  note: {n}")


def design_json(design, extra=None):
    sp = design.spec
    return {
        'spec': asdict(sp), 'substrate': asdict(design.sub), 'kf': design.kf, 'kb': design.kb,
        'g': design.g, 'targets_GHz': {k: v / 1e9 for k, v in targets(sp).items()},
        'elements': [dict(zip(('name', 'z_ohm', 'width_mm', 'length_mm', 'gap_mm'), r)) for r in design.rows],
        'board_mm': [design.geometry.W, design.geometry.H], 'notes': design.notes,
        # the layout as drawn (mm, KiCad's y down): copper polygons, ground vias, port markers
        'geometry': {'polys': design.geometry.polys, 'vias': design.geometry.vias, 'ports': design.geometry.ports,
                     'via_mm': [VIA_DRILL, VIA_SIZE]},
        **(extra or {}),
    }


def write_model_s2p(design, path, f):
    s11, s21 = model_response(design, f)
    with open(path, 'w') as fh:
        fh.write("! dfsynth line model of the layout (port to port)\n# GHz S RI R 50\n")
        for fi, a, b in zip(f, s11, s21):
            fh.write(f"{fi / 1e9:.6f} {a.real:.6e} {a.imag:.6e} {b.real:.6e} {b.imag:.6e} "
                     f"{b.real:.6e} {b.imag:.6e} {a.real:.6e} {a.imag:.6e}\n")


def main(argv=None):
    a = parser().parse_args(argv)
    if a.list:
        list_all()
        return 0
    if not a.type or not a.f0:
        parser().error("--type and --f0 are required (or --list)")
    topo = a.topology or KINDS[a.type][0]
    sub = make_substrate(a)
    spec = Spec(a.type, topo, a.f0, a.bw, a.response, a.order or 5, a.ripple, a.z0,
                a.zhigh, a.zlow, a.zstub, a.feed, stub_angle=a.stub_angle, hairpin_gap=a.hairpin_gap)
    try:
        spec.check()
        if a.atten and not a.order:
            spec.order = order_from_atten(spec, a.atten)
            print(f"order {spec.order} meets {a.atten}")
        if a.no_calibrate:
            notes = prepare(spec)
            design = build(spec, sub)
            design.notes = notes + design.notes
        else:
            design = calibrate(spec, sub, log=print, refine_time=a.refine_time)
    except ValueError as e:
        print(f"dfsynth: {e}", file=sys.stderr)
        return 2
    describe(design)
    if violations(design) and not a.allow_fab_violations:
        _refuse(design, sub)
        return 2

    base = a.out
    title = f"{spec.kind} {spec.topology} {_fmt(spec.f0)}"
    pcb, cfg = kicad.write(design, base, title)
    f = sweep(spec, 801)
    write_model_s2p(design, base + '_model.s2p', f)
    s11, s21 = model_response(design, f)
    mm = val.metrics(design, f, s21, s11)
    print(f"\nline model (with loss): edges {_edges(mm)}  centre {_pct(mm['centre_error_pct'])}  "
          f"bw {_pct(mm['bandwidth_error_pct'])}  IL {mm.get('passband_IL_dB', float('nan')):.2f} dB")
    val.plot(design, {'model (dfsynth)': (f, s21, s11)}, base + '_model.png')
    print(f"wrote {pcb}\nwrote {cfg}\nwrote {base}_model.s2p, {base}_model.png")
    summary = {'model': mm}

    if a.validate:
        extra = a.k2e_args.split() if a.k2e_args else []
        rounds = []
        target = spec                                         # the design the line model puts on the spec
        for r in range(a.em_tune + 1):
            tag = '' if r == 0 else f'_tune{r}'
            print(f"\nkicad2ems ({a.validate}){' round ' + str(r) if a.em_tune else ''}:")
            try:
                mets, results = val.validate(design, base, a.validate, extra, tag=tag)
            except RuntimeError as e:
                print(f"dfsynth: {e}", file=sys.stderr)
                return 3
            val.plot(design, results, base + f'_validation{tag}.png')
            for name, m in mets.items():
                print(f"  {name:16s} edges {_edges(m)}  centre {_pct(m['centre_error_pct'])}  "
                      f"bw {_pct(m['bandwidth_error_pct'])}  IL {m.get('passband_IL_dB', float('nan')):.2f} dB  "
                      f"RL {m.get('passband_RL_worst_dB', float('nan')):.1f} dB "
                      f"(own band {m.get('own_passband_RL_worst_dB', float('nan')):.1f})")
            fw = val.full_wave(mets)
            pred = val.predicted(mets, fw) if fw else None
            if pred:
                print(f"  {fw} with {design.sub.t * 1e3:.0f} um copper (its error over the t=0 model, applied to "
                      f"the model): centre {_pct(pred[0])}  bw {_pct(pred[1])}")
            rounds.append({'kf': design.kf, 'kb': design.kb, 'metrics': mets,
                           'predicted_real_copper_pct': pred})
            if r == a.em_tune or fw is None:
                break
            if spec.topology == 'interdigital':
                # each resonator's frequency, each coupling and the taps can be off on their own: a
                # centre / bandwidth correction cannot move them back into line
                fe, s21e, s11e = results[fw]
                new = space_map(target, design.spec, sub, design.kf, design.kb, fe, s21e, s11e, log=print)
                if new is None:
                    print(f"  the line model does not reproduce the {fw} result: stopping the tuning")
                    break
                notes = [n for n in design.notes if not isinstance(n, FabViolation)]
                design = build(new, sub, design.kf, design.kb)
                design.notes = notes + violations(design)
                describe(design)
                if violations(design) and not a.allow_fab_violations:
                    print("  the corrected design is below the fabrication limits: stopping the tuning")
                    break
                kicad.write(design, base, title)
                continue
            if pred is None:
                print("  no band edges in the full-wave result: stopping the tuning")
                break
            rc = 1 + pred[0] / 100
            rb = 1 + pred[1] / 100 if pred[1] is not None else None
            if rb is not None and not 0.5 < rb < 2:
                print(f"  bandwidth error {pred[1]:+.0f}% is too large to tune from (a distorted pass band?): "
                      "tuning the centre only")
                rb = None
            if rc is None:
                print("  no band edges in the full-wave result: stopping the tuning")
                break
            kf = design.kf / rc
            kb = design.kb / rb ** 0.8 if rb and spec.kind != 'lowpass' else design.kb
            print(f"  tuning from {fw}: kf {design.kf:.4f} -> {kf:.4f}, kb {design.kb:.4f} -> {kb:.4f}")
            notes = [n for n in design.notes if not isinstance(n, FabViolation)]
            design = build(spec, sub, kf, kb)
            design.notes = notes + violations(design)
            describe(design)
            if violations(design) and not a.allow_fab_violations:
                print("  the corrected design is below the fabrication limits: stopping the tuning")
                break
            kicad.write(design, base, title)
        summary['em'] = rounds
        print(f"wrote {base}_validation*.png")
    val.save_json(base + '_design.json', design_json(design, summary))
    print(f"wrote {base}_design.json")
    return 0


def reachable_bw(spec, sub, kf=1.0, kb=1.0):
    """The bandwidth nearest the spec's whose synthesis meets the fabrication limits (None if none in
    0.05 to 3 x the spec's, or for a type without one)."""
    if spec.kind not in ('bandpass', 'bandstop'):
        return None
    from dataclasses import replace

    def ok(k):
        try:
            return not violations(build(replace(spec, bw=spec.bw * k), sub, kf, kb))
        except ValueError:
            return False
    ks = sorted(np.geomspace(0.05, 3.0, 61), key=lambda k: abs(math.log(k)))
    good = next((k for k in ks if ok(k)), None)
    if good is None:
        return None
    bad = 1.0                                              # bisect between the spec (fails) and the first fit
    for _ in range(30):
        mid = math.sqrt(good * bad)
        good, bad = (mid, bad) if ok(mid) else (good, mid)
    return spec.bw * good


def _refuse(design, sub):
    print("\ndfsynth: the design cannot be made as asked "
          f"(fabrication limits --min-width {sub.min_width}, --min-gap {sub.min_gap} mm):", file=sys.stderr)
    for v in violations(design):
        print(f"  {v}", file=sys.stderr)
    bw = reachable_bw(design.spec, sub, design.kf, design.kb)
    if bw:
        print(f"  the nearest bandwidth that meets them is about {_fmt(bw)} (asked {_fmt(design.spec.bw)})",
              file=sys.stderr)
    print("  other ways out: another topology or substrate, or --allow-fab-violations to write it anyway",
          file=sys.stderr)


def _pct(v):
    return f"{v:+6.2f}%" if v is not None else '   n/a '


def _edges(m):
    return ' '.join(f"{k} {v:.4f}" if v else f"{k} n/a" for k, v in m['edges_GHz'].items())


if __name__ == '__main__':
    sys.exit(main())
