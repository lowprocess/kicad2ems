"""
kicad2ems.core.rlgc - per-unit-length R(f), L(f), G(f), C(f) of transmission
lines: a cross-section given by its sizes, or every distinct cross-section of a
board's nets (each coupled group and each single trace as the line model cuts
the board across it). R and L have the skin and proximity effects and the
return's own crowding in the planes (kicad2ems.core.lines.mqs: a 2D
eddy-current solve with the copper meshed inside); G and C are the field
solver's (kicad2ems.core.lines.xsection).

    python -m kicad2ems.core.rlgc board.kicad_pcb --nets 'DQ*,CLK*' --out o
        [--layers F.Cu,In2.Cu] [--min-length 1]
    python -m kicad2ems.core.rlgc --xs microstrip --w 0.15 --lines 2
        --gap 0.15 --h 0.2 --er 4.3 --t 0.035 --out o
    python -m kicad2ems.core.rlgc --xs stripline --w 0.1 --h 0.15 --h2 0.15
        --er 3.7 --t 0.018 --out o
    python -m kicad2ems.core.rlgc --xs section.json --out o
        # an xsection.CrossSection's to_dict()
      common: [--fmin 0.001] [--fmax 20] GHz [--per-decade 5] [--sigma 5.8e7]
              [--rough um] [--diel fixed|wideband] [--diel-fref 1] GHz
              [--length mm] [--z0 50]

Each section gets
  <out>/<name>.rlgc       the W-element's RLGC file (HSPICE's RLGCfile=): Lo
                          the external inductance, Co, Ro the DC resistance,
                          Go 0, Rs and Gd fitted (R = Ro + Rs sqrt(f) (1 + j),
                          G = Gd f): the analytic model
  <out>/<name>_w.inc      the W-element's tabular model (MODELTYPE=TABLE: the
                          four `sp` models, every frequency, DC first)
  <out>/<name>.s<2N>p     its S-parameters over its length (ports 1..N the near
                          ends, N+1..2N the far ends), reference z0
  <out>/rlgc_<name>.png   R and L of each line over frequency (the FIGURES
                          longest sections)
and <out>/rlgc.json every section: its nets, layer, length, cross-section, the
matrices at every frequency, its modes' attenuation and impedance at fmax.

On a board, the nets are those whose names match a pattern (fnmatch, as
core.coupling has them); the line model of those nets is built with every
section on the board's cut (field 'all'), the reference copper every net that
is not a routed signal (ipc's impedance map's rule). Sections of one net (or
one group of nets) on one layer with the same cross-section are one, their
lengths added; a section shorter than --min-length mm is left out (a pad's
breakout), and so is a stretch of no reference copper. The planes' and the
other layers' copper get their own layers' thickness.
"""
import argparse
import fnmatch
import json
import math
import os
import re
import sys

import numpy as np

RESULT = 'rlgc.json'
FORMAT = 'kicad2ems-rlgc'
MIN_LENGTH = 1.0
PER_DECADE = 5
# the longest sections that get a figure of their own (a figure takes about
# as long as a section's solve; every section's curves are in the result)
FIGURES = 12


# ---- the frequencies ---------------------------------------------------------
def frequencies(fmin_ghz, fmax_ghz, per_decade=PER_DECADE):
    """DC, then fmin to fmax (GHz) per_decade points a decade (Hz)."""
    lo, hi = float(fmin_ghz) * 1e9, float(fmax_ghz) * 1e9
    if not 0 < lo < hi:
        raise ValueError("--fmin must be above 0 and below --fmax")
    n = max(2, int(round(math.log10(hi / lo) * per_decade)) + 1)
    return np.r_[0.0, np.geomspace(lo, hi, n)]


# ---- a cross-section from its sizes -----------------------------------------
def given_section(a):
    """The CrossSection the command line describes (--xs microstrip,
    stripline, or a JSON file)."""
    from .lines import xsection as X
    if a.xs not in ('microstrip', 'stripline'):
        with open(a.xs, encoding='utf-8') as fh:
            return X.CrossSection.from_dict(json.load(fh)), 'file'
    if a.w is None or a.h is None or a.er is None:
        raise ValueError(f"--xs {a.xs}: give --w, --h and --er")
    w = [a.w] * a.lines if a.lines > 1 else a.w
    if a.xs == 'microstrip':
        xs = X.microstrip(w, a.h, a.er, a.t, a.tand, mask=a.mask, s=a.s,
                          gap=a.gap)
    else:
        xs = X.stripline(w, a.h, a.h2 if a.h2 is not None else a.h, a.er,
                         a.t, a.tand, s=a.s, gap=a.gap)
    return xs, a.xs


# ---- the sections of a board ------------------------------------------------
def _board(path):
    """The Board of a .kicad_pcb (or a config's board), every net kept."""
    from .board import Board
    from .importers import open_board
    if str(path).lower().endswith('.toml'):
        from .config import build_settings
        path = build_settings({}, config=path).board
    return Board(open_board(path), nets=None, need_ports=False, parts='none',
                 log=lambda *a: None)


def _layer_t(m, z, default):
    """The copper thickness of the layer at height z (the model's), or
    default."""
    for name, zl in m.cu_z.items():
        if abs(zl - z) < 1e-6:
            return float(m.cu_t.get(name, default) or default)
    return default


def _thicknesses(m, args):
    """({plane height: mm}, {sheet height: mm}) of a cut's other layers, at the
    heights _strips_section moves them to, each its layer's copper."""
    _, zs, t, up, _, layers, _ = args
    zmap = ((lambda z: z + t if z > zs + 1e-9 else z) if up else
            (lambda z: z - t if z < zs - 1e-9 else z))
    planes, sheets = {}, {}
    for z, full, _ in layers:
        (planes if full else sheets)[round(zmap(z), 9)] = _layer_t(
            m, z, t or 0.035)
    planes['default'] = sheets['default'] = t or 0.035
    return planes, sheets


def board_sections(path, patterns, layers=None, min_length=MIN_LENGTH,
                   echo=print):
    """(sections, left out, the board's nets matched): each distinct
    cross-section of the nets matching `patterns` on the board, as the line
    model cuts it: [dict(name, nets, layer, kind, length_mm, pieces, source,
    xs, plane_t, sheet_t)]."""
    from .lines import builder, mqs, reference
    m = _board(path)
    routed = {t['net'] for t in m.prims.tracks if t['layer'] in m.cu_z}
    signal = sorted(n for n in routed - set(m.ground_nets) if n)
    pats = [p.strip() for p in patterns if p.strip()]
    nets = [n for n in signal
            if any(fnmatch.fnmatchcase(n, p) for p in pats)]
    if not nets:
        raise ValueError(
            f"no routed signal net matches {', '.join(pats)} (the board's: "
            f"{', '.join(signal[:12])}{' ...' if len(signal) > 12 else ''})")
    echo(f"rlgc: {len(nets)} net(s): {', '.join(nets[:8])}"
         + (' ...' if len(nets) > 8 else ''))
    refcu = reference.RefCopper(m, set(signal))
    lm = builder.build(m, nets=nets, refcu=refcu, field='all')
    found, left = {}, []

    def add(nets_, layer, kind, length, xs, source, pt, st):
        key = (tuple(nets_), layer, json.dumps(xs.to_dict()))
        s = found.get(key)
        if s is None:
            s = found[key] = dict(nets=list(nets_), layer=layer, kind=kind,
                                  length_mm=0.0, pieces=0, source=source,
                                  xs=xs, plane_t=pt, sheet_t=st)
        s['length_mm'] += float(length)
        s['pieces'] += 1

    for g in lm.groups:
        legs = [e for e, _, _ in g['members']]
        layer = legs[0].layer
        if layers and layer not in layers:
            continue
        names = [e.net for e in legs]
        if g.get('cut'):
            xs = reference._strips_section(*g['cut'])
            pt, st = _thicknesses(m, g['cut'])
            src = 'cut'
        elif legs[0].coupled:
            xs, src = legs[0].coupled['section'], 'ideal planes'
            pt = st = legs[0].t or 0.035
        else:
            left.append(dict(nets=names, layer=layer, length_mm=g['length'],
                             why="no field-solved cross-section"))
            continue
        add(names, layer, 'coupled', g['length'], xs, src, pt, st)
    for e in lm.elements:
        if e.kind != 'trace' or e.group is not None or not e.net:
            continue
        if layers and e.layer not in layers:
            continue
        got = mqs.section_of(e)
        if got is None:
            left.append(dict(nets=[e.net], layer=e.layer, length_mm=e.length,
                             why=f"no reference copper ({e.xs})"))
            continue
        xs, pt = got
        st = pt
        src = 'cut' if e.cut else 'ideal planes'
        if e.cut:
            pt, st = _thicknesses(m, e.cut)
        add([e.net], e.layer, 'single', e.length, xs, src, pt, st)
    out = []
    for s in found.values():
        if s['length_mm'] < min_length:
            left.append(dict(nets=s['nets'], layer=s['layer'],
                             length_mm=s['length_mm'],
                             why=f"shorter than {min_length:g} mm"))
            continue
        out.append(s)
    out.sort(key=lambda s: (s['layer'], s['nets'], -s['length_mm']))
    seen = {}
    for s in out:
        base = _safe('+'.join(s['nets']) + '@' + s['layer'])
        seen[base] = seen.get(base, 0) + 1
        s['name'] = base if seen[base] == 1 else f"{base}_{seen[base]}"
    return out, left, nets


def _safe(text):
    return re.sub(r'[^A-Za-z0-9_.+@-]+', '_', text).strip('_')[:60] or 'line'


# ---- what a section gives ---------------------------------------------------
def modes(r, i):
    """The modes of the lines at frequency index i: [(attenuation dB/mm,
    eeff)], the fastest first (a single line's one; a pair's even mode is the
    slower on microstrip)."""
    from .constants import C0
    w = 2 * math.pi * r.f[i]
    if w <= 0:
        return []
    Z = r.R[i] + 1j * w * r.L[i]
    Y = r.G[i] + 1j * w * r.C[i]
    g = np.sqrt(np.linalg.eigvals(Z @ Y))
    g = np.where(g.real < 0, -g, g)
    out = [(float(x.real) * 8.685889638 / 1e3, float((x.imag * C0 / w) ** 2))
           for x in g]
    return sorted(out, key=lambda m: m[1])


def impedances(r):
    """The lossless impedances: a single line's Z0, a pair's differential and
    common (2 Zodd, Zeven / 2), from C and C_air (as xsection gives them)."""
    from .lines.xsection import mode
    C, Ca = r.C[0], r.C_air
    if r.n == 1:
        z0, ee = mode(C[0, 0], Ca[0, 0])
        return dict(z0=z0, eeff=ee)
    if r.n == 2:
        c11, ca11 = 0.5 * (C[0, 0] + C[1, 1]), 0.5 * (Ca[0, 0] + Ca[1, 1])
        ze, _ = mode(c11 + C[0, 1], ca11 + Ca[0, 1])
        zo, _ = mode(c11 - C[0, 1], ca11 - Ca[0, 1])
        return dict(zdiff=2 * zo, zcommon=ze / 2)
    return {}


def write_rlgc(path, r, title=''):
    """The W-element's RLGC file (HSPICE's RLGCfile=): N, then the lower
    triangles of Lo (the external inductance, H/m), Co (F/m), Ro (the DC
    resistance, ohm/m), Go (0), Rs (ohm/(m sqrt(Hz))) and Gd (S/(m Hz)) of
    R(f) = Ro + sqrt(f) (1 + j) Rs and G(f) = f Gd: Rs fitted where the skin
    is thinner than the copper (R - Ro above Ro; else the top third of the
    band), Gd at the top frequency."""
    n = r.n
    f = r.f
    dc = int(np.argmin(f))
    Ro = r.R[dc]
    sel = (f > 0) & (np.abs(np.diagonal(r.R, axis1=1, axis2=2)
                            - np.diagonal(Ro)).min(1) > np.diagonal(Ro).max())
    if sel.sum() < 2:
        sel = f >= f[f > 0][len(f[f > 0]) * 2 // 3]
    sq = np.sqrt(f[sel])
    Rs = np.einsum('k,kij->ij', sq, r.R[sel] - Ro[None]) / float(sq @ sq)
    top = int(np.argmax(f))
    Gd = r.G[top] / f[top]
    tri = lambda M: '\n'.join(' '.join(f"{M[i, j]:.6e}" for j in range(i + 1))
                              for i in range(n))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(f"* kicad2ems RLGC {title}\n* N (number of signal "
                 "conductors)\n" + f"{n}\n")
        for name, M in (('Lo', r.L_inf), ('Co', r.C[top]), ('Ro', Ro),
                        ('Go', np.zeros((n, n))), ('Rs', Rs), ('Gd', Gd)):
            fh.write(f"* {name}\n{tri(M)}\n")
    return path


def write_table(path, r, name):
    """The W-element's tabular model (MODELTYPE=TABLE): `sp` models of L, C,
    R and G over every frequency (lower triangles, SPACING=NONUNIFORM), and
    the W model naming them; a W element then takes TABLEMODEL=<name>."""
    n = r.n
    tri = lambda M: ' '.join(f"{M[i, j]:.6e}" for i in range(n)
                             for j in range(i + 1))
    order = np.argsort(r.f)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(f"* kicad2ems tabular RLGC: {name}\n"
                 f"* W1 in1 .. in{n} 0 out1 .. out{n} 0 N={n} L=<m> "
                 f"TABLEMODEL={name}\n")
        fh.write(f".MODEL {name} W MODELTYPE=TABLE N={n}\n"
                 f"+ LMODEL={name}_L CMODEL={name}_C RMODEL={name}_R "
                 f"GMODEL={name}_G\n")
        for key, M, inf in (('L', r.L, r.L_inf), ('C', r.C, None),
                            ('R', r.R, None), ('G', r.G, None)):
            fh.write(f".MODEL {name}_{key} sp N={n} SPACING=NONUNIFORM "
                     "VALTYPE=REAL MATRIX=SYMMETRIC\n")
            if inf is not None:
                fh.write(f"+ INFINITY=({tri(inf)})\n")
            fh.write(f"+ DATA=({len(order)}\n")
            for i in order:
                fh.write(f"+ {r.f[i]:.6e} {tri(M[i])}\n")
            fh.write("+ )\n")
    return path


def write_sparams(path, r, length_mm, z0, names):
    """The S-parameters of length_mm of the lines (above DC), Touchstone."""
    from .lines.coupled import zy_s
    from .touchstone import write_touchstone
    keep = r.f > 0
    S = zy_s(r.z()[keep], r.y()[keep], length_mm, z0)
    ports = ([f"{nm} near" for nm in names] + [f"{nm} far" for nm in names])
    write_touchstone(path, r.f[keep], S, z0,
                     comment=f"RLGC lines, {length_mm:g} mm", names=ports)
    return path


def plot(r, names, path, title):
    """R and L of each line (its self terms) over frequency."""
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(figsize=(9, 3.6), dpi=100)
        FigureCanvasAgg(fig)
        a, b = fig.add_subplot(121), fig.add_subplot(122)
        f = r.f[r.f > 0]
        for k, nm in enumerate(names):
            a.loglog(f / 1e9, r.R[r.f > 0, k, k], label=nm)
            b.semilogx(f / 1e9, r.L[r.f > 0, k, k] * 1e9, label=nm)
        b.semilogx(f / 1e9, np.full(len(f), r.L_inf[0, 0] * 1e9), 'k:',
                   lw=1, label=f"{names[0]}: L ext")
        a.set(xlabel='GHz', ylabel='R, ohm/m', title='resistance')
        b.set(xlabel='GHz', ylabel='L, nH/m', title='inductance')
        for ax in (a, b):
            ax.grid(True, which='both', alpha=0.3)
        b.legend(fontsize=7)
        fig.suptitle(title, fontsize=9)
        # (fixed margins: tight_layout was half of a board's run, 0.24 s a
        # figure)
        fig.subplots_adjust(left=0.08, right=0.98, bottom=0.14, top=0.86,
                            wspace=0.28)
        fig.savefig(path)
        return path
    except Exception:  # noqa: BLE001 (a figure never fails the run)
        return None


# ---- the run ----------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(
        prog='python -m kicad2ems.core.rlgc',
        description="Per-unit-length R, L, G, C of transmission lines: skin "
        "and proximity effect from a 2D eddy-current solve")
    p.add_argument('board', nargs='?', help="a .kicad_pcb or a config (.toml)")
    p.add_argument('--nets', default='',
                   help="the board's nets, name patterns, comma-separated")
    p.add_argument('--layers', default='',
                   help="only sections on these layers, comma-separated")
    p.add_argument('--min-length', type=float, default=MIN_LENGTH,
                   help="a board's section shorter than this is left out, mm")
    p.add_argument('--xs', help="a cross-section by its sizes: microstrip, "
                   "stripline, or a CrossSection JSON file")
    p.add_argument('--w', type=float, help="strip width, mm")
    p.add_argument('--lines', type=int, default=1, help="side-by-side strips")
    p.add_argument('--gap', type=float, default=0.0, help="between strips, mm")
    p.add_argument('--h', type=float, help="dielectric under the strip, mm")
    p.add_argument('--h2', type=float,
                   help="stripline: from the strip's top to the upper plane")
    p.add_argument('--er', type=float, help="relative permittivity")
    p.add_argument('--tand', type=float, default=0.0, help="loss tangent")
    p.add_argument('--t', type=float, default=0.035, help="copper, mm")
    p.add_argument('--mask', type=float, default=0.0,
                   help="microstrip: solder mask over it, mm")
    p.add_argument('--s', type=float,
                   help="coplanar ground this far from the outer strips, mm")
    p.add_argument('--fmin', type=float, default=0.001, help="GHz")
    p.add_argument('--fmax', type=float, default=20.0, help="GHz")
    p.add_argument('--per-decade', type=int, default=PER_DECADE,
                   help="frequencies a decade (DC as well)")
    p.add_argument('--sigma', type=float, default=5.8e7,
                   help="copper conductivity, S/m")
    p.add_argument('--rough', type=float, default=0.0,
                   help="copper roughness, um RMS (Hammerstad)")
    p.add_argument('--diel', choices=('fixed', 'wideband'), default='fixed',
                   help="wideband: each dielectric's Djordjevic-Sarkar curve")
    p.add_argument('--diel-fref', type=float, default=1.0,
                   help="where er and tan d are given, GHz (wideband)")
    p.add_argument('--length', type=float,
                   help="the Touchstone's length, mm (default: a board "
                   "section's own; 10 mm for a given cross-section)")
    p.add_argument('--z0', type=float, default=50.0,
                   help="the Touchstone's reference impedance, ohm")
    p.add_argument('--out', required=True, help="the output folder")
    return p


def run(a, echo=print):
    """Solve and write; returns the result dict."""
    from .lines import mqs
    os.makedirs(a.out, exist_ok=True)
    f = frequencies(a.fmin, a.fmax, a.per_decade)
    if a.board:
        pats = [p for p in re.split(r'[,\s]+', a.nets or '') if p]
        if not pats:
            raise ValueError("name the nets: --nets 'DQ*,CLK'")
        lays = [x.strip() for x in (a.layers or '').split(',') if x.strip()]
        secs, left, nets = board_sections(a.board, pats, lays or None,
                                          a.min_length, echo)
        if not secs:
            raise ValueError("no section of those nets to solve"
                             + (f" ({left[0]['why']})" if left else ''))
    else:
        if not a.xs:
            raise ValueError("give a board and --nets, or --xs")
        xs, src = given_section(a)
        n = len(xs.signals)
        secs = [dict(name=_safe(f"{a.xs if src != 'file' else 'section'}"
                                f"{'_x' + str(n) if n > 1 else ''}"),
                     nets=[f"line{k + 1}" for k in range(n)], layer='',
                     kind='single' if n == 1 else 'coupled',
                     length_mm=a.length or 10.0, pieces=1, source=src, xs=xs,
                     plane_t=a.t or 0.035, sheet_t=a.t or 0.035)]
        left, nets = [], []
    wide = a.diel_fref * 1e9 if a.diel == 'wideband' else None
    out = []
    top = int(np.argmax(f))
    solved = {}          # a cross-section and its mirror image: one solve
    drawn = {id(s) for s in sorted(secs, key=lambda s: -s['length_mm'])
             [:FIGURES]}
    for k, s in enumerate(secs):
        key = mqs.section_key(s['xs'], json.dumps(s['plane_t'], default=str),
                              json.dumps(s['sheet_t'], default=str))
        r = solved.get(key)
        if r is None:
            r = solved[key] = mqs.rlgc(s['xs'], f, a.sigma, s['plane_t'],
                                       s['sheet_t'], rough=a.rough or None,
                                       wideband=wide)
        length = a.length or s['length_mm']
        name = s['name']
        files = dict(
            rlgc=os.path.basename(write_rlgc(
                os.path.join(a.out, name + '.rlgc'), r, name)),
            table=os.path.basename(write_table(
                os.path.join(a.out, name + '_w.inc'), r,
                re.sub(r'[^A-Za-z0-9_]', '_', name))),
            touchstone=os.path.basename(write_sparams(
                os.path.join(a.out, f"{name}.s{2 * r.n}p"), r, length, a.z0,
                s['nets'])))
        img = (plot(r, s['nets'], os.path.join(a.out, f"rlgc_{name}.png"),
                    f"{name}: {', '.join(s['nets'])}")
               if id(s) in drawn else None)
        if img:
            files['figure'] = os.path.basename(img)
        md = modes(r, top)
        imp = impedances(r)
        i = top
        echo(f"rlgc: {name} ({s['kind']}, {s['length_mm']:.1f} mm, "
             f"{r.nodes} nodes): at {f[i] / 1e9:g} GHz R "
             + ', '.join(f"{r.R[i, j, j]:.3g}" for j in range(r.n))
             + " ohm/m, L " + ', '.join(f"{r.L[i, j, j] * 1e9:.1f}"
                                         for j in range(r.n))
             + " nH/m; loss " + ', '.join(f"{m[0]:.4f}" for m in md)
             + " dB/mm" + ''.join(f", {k_} {v:.1f}" for k_, v in imp.items()
                                  if k_ != 'eeff'))
        out.append(dict(
            name=name, nets=s['nets'], layer=s['layer'], kind=s['kind'],
            length_mm=s['length_mm'], pieces=s['pieces'], source=s['source'],
            touchstone_mm=length, xs=s['xs'].to_dict(), rlgc=r.to_dict(),
            nodes=r.nodes, notes=r.notes, files=files,
            at_fmax=dict(
                f_hz=float(f[i]),
                R=[float(r.R[i, j, j]) for j in range(r.n)],
                L=[float(r.L[i, j, j]) for j in range(r.n)],
                R_dc=[float(r.R[0, j, j]) for j in range(r.n)],
                modes=[dict(loss_db_mm=m[0], eeff=m[1]) for m in md],
                **{k_: float(v) for k_, v in imp.items()})))
    res = dict(format=FORMAT, version=1, board=a.board or None,
               patterns=a.nets or None, nets=nets, f=f.tolist(),
               sigma=a.sigma, rough=a.rough or None, diel=a.diel,
               z0=a.z0, sections=out, left_out=left)
    with open(os.path.join(a.out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    echo(f"rlgc: {len(out)} section(s) -> {os.path.join(a.out, RESULT)}")
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.core.rlgc: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


# ---- the question, as the study has it (kicad2ems.core.analyses) ------------
from .analyses import Analysis                                      # noqa: E402


def _result(folder):
    try:
        with open(os.path.join(folder or '', RESULT), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if d.get('format') == FORMAT else None
    except (OSError, ValueError):
        return None


class LineParameters(Analysis):
    """Per-unit-length RLGC of a board's lines (this module)."""
    name, label, tool = 'rlgc', 'Line parameters', 'kicad2ems.core.rlgc'
    about = ("each distinct cross-section of the named nets: R, L, G, C over "
             "frequency (skin and proximity effect), W-element models")
    order = 18
    result = RESULT
    provides = ('rlgc',)
    engine = 'field solver'

    def check(self):
        try:
            import scipy  # noqa: F401
        except ImportError:
            return False, "scipy is not installed"
        return True, "2D eddy-current and electrostatic field solvers"

    def options(self):
        from .options import Option, split
        o = lambda key, kind, default, help, group='Nets', **kw: Option(
            key, default, help, dict(list=list, float=float,
                                     int=int).get(kind, str),
            group=group, conv=split if kind == 'list' else None, **kw)
        return [
            o('nets', 'list', [],
              "the nets, by name patterns (DQ*, CLK_[PN]; one a line, or "
              "comma-separated)", level='basic', pick='nets'),
            o('layers', 'str', '',
              "only sections on these copper layers, comma-separated (empty: "
              "every layer)"),
            o('min_length', 'float', MIN_LENGTH,
              "a section shorter than this is left out (a breakout), mm"),
            o('fmin', 'float', 0.001, "the lowest frequency (DC as well), GHz",
              'Solve'),
            o('fmax', 'float', 20.0, "the highest frequency, GHz", 'Solve',
              level='basic'),
            o('per_decade', 'int', PER_DECADE, "frequencies a decade",
              'Solve'),
            o('sigma', 'float', 5.8e7, "copper conductivity, S/m", 'Solve'),
            o('rough', 'float', '', "copper roughness, um RMS (Hammerstad)",
              'Solve'),
            o('diel', 'str', 'fixed',
              "the dielectric: fixed er and tan d, or wideband "
              "(Djordjevic-Sarkar)", 'Solve', choices=('fixed', 'wideband')),
            o('z0', 'float', 50.0,
              "the Touchstone files' reference impedance, ohm", 'Solve'),
        ]

    @staticmethod
    def _nets(values):
        v = values.get('nets') or []
        v = (v.replace('\n', ',').split(',') if isinstance(v, str) else
             [x for item in v for x in str(item).split(',')])
        return [x.strip() for x in v if x.strip()]

    def tag(self, values):
        nets = self._nets(values)
        return ('_'.join(re.sub(r'[^A-Za-z0-9]+', '', n)[-10:]
                         for n in nets[:3]) or 'run')

    def command(self, values, board, out, preview=False, results=None):
        nets = self._nets(values)
        if not board:
            raise ValueError("choose the board")
        if not nets:
            raise ValueError("name the nets: patterns such as DQ*")
        cmd = [sys.executable, '-m', 'kicad2ems.core.rlgc', str(board),
               '--nets', ','.join(nets), '--out', str(out)]
        for key in ('layers', 'min_length', 'fmin', 'fmax', 'per_decade',
                    'sigma', 'rough', 'diel', 'z0'):
            v = values.get(key)
            if v not in (None, '', []):
                cmd += ['--' + key.replace('_', '-'), str(v)]
        return cmd

    def status(self, line):
        line = line.strip()
        return line[:160] if line.startswith('rlgc:') else None

    def report(self, folder):
        d = _result(folder)
        if d is None:
            return None
        secs = d['sections']
        fmax = d['f'][-1] / 1e9 if d['f'] else 0
        rows = []
        for s in secs:
            a = s['at_fmax']
            z = (f"{a['z0']:.1f}" if 'z0' in a else
                 f"{a['zdiff']:.1f} diff" if 'zdiff' in a else '')
            rows.append([s['name'], ', '.join(s['nets']), s['layer'] or '-',
                         s['length_mm'], z,
                         ', '.join(f"{x:.3g}" for x in a['R_dc']),
                         ', '.join(f"{x:.3g}" for x in a['R']),
                         ', '.join(f"{x * 1e9:.1f}" for x in a['L']),
                         ', '.join(f"{m['loss_db_mm']:.4f}"
                                   for m in a['modes'])])
        head = (f"{len(secs)} cross-section(s) solved, DC to {fmax:g} GHz: "
                "R and L with the skin and proximity effect (a 2D "
                "eddy-current solve), G and C from the field solver")
        sec = [dict(
            title="The sections",
            text="Each distinct cross-section of the nets (a coupled group's "
            "lines together), its length on the board; at the top "
            f"frequency ({fmax:g} GHz) each line's own R and L, and the "
            "attenuation of each of its modes (conductor and dielectric "
            "loss). Each section's W-element RLGC file, tabular model and "
            "Touchstone are in the folder.",
            table=dict(columns=['section', 'nets', 'layer', 'length (mm)',
                                'Z0 (ohm)', 'R DC (ohm/m)',
                                'R (ohm/m)', 'L (nH/m)', 'loss (dB/mm)'],
                       rows=rows,
                       formats=[None, None, None, '.1f', None, None, None,
                                None, None]))]
        if d.get('left_out'):
            sec.append(dict(
                title="Left out",
                table=dict(columns=['nets', 'layer', 'length (mm)', 'why'],
                           rows=[[', '.join(x['nets']), x['layer'],
                                  x['length_mm'], x['why']]
                                 for x in d['left_out']],
                           formats=[None, None, '.1f', None])))
        notes = sorted({n for s in secs for n in s.get('notes', [])})
        images = [os.path.join(folder, s['files']['figure']) for s in secs
                  if s['files'].get('figure')][:6]
        return dict(title=f"Line parameters: {d.get('patterns') or 'given'}",
                    summary=head, sections=sec, notes=notes,
                    images=[p for p in images if os.path.isfile(p)],
                    footer="kicad2ems.core.rlgc"
                    + (f", {os.path.basename(d['board'])}" if d.get('board')
                       else ''))

    def curves(self, folder):
        d = _result(folder)
        if d is None:
            return []
        out = []
        for key, ylabel, scale, logy in (('R', 'ohm/m', 1.0, True),
                                         ('L', 'nH/m', 1e9, False)):
            cs = []
            for s in d['sections']:
                f = np.asarray(s['rlgc']['f'])
                M = np.asarray(s['rlgc'][key])
                for j, nm in enumerate(s['nets']):
                    cs.append(dict(name=f"{s['name']}: {nm}",
                                   x=(f[f > 0] / 1e9).tolist(),
                                   y=(M[f > 0, j, j] * scale).tolist(),
                                   unit=ylabel))
            out.append(dict(title=f"{key} per metre", xlabel='GHz',
                            ylabel=ylabel, logx=True, logy=logy, curves=cs))
        return out

    def figures(self, folder):
        d = _result(folder)
        if d is None:
            return []
        out = []
        for s in d['sections']:
            a = s['at_fmax']
            for j, nm in enumerate(s['nets']):
                tag = f"{nm} ({s['name']})"
                out.append(dict(name=f"R {tag}", value=a['R'][j],
                                unit='ohm/m'))
                out.append(dict(name=f"L {tag}", value=a['L'][j] * 1e9,
                                unit='nH/m'))
            for k, m in enumerate(a['modes']):
                out.append(dict(name=f"loss {s['name']}"
                                + (f" mode {k + 1}" if len(a['modes']) > 1
                                   else ''),
                                value=m['loss_db_mm'], unit='dB/mm'))
        return out
