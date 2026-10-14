"""
mixed: mom25d for the board, its long straight runs of trace as transmission lines (segmentation).

A long uniform line costs mom25d unknowns all along it, and the line model already knows it: per unit length
its gamma and Zc. Here the middle of each long, lone, straight run of signal trace is cut out of the board;
mom25d solves what is left with a de-embedded wave port on each face of each cut (characterised on the line's own
cross-section: the planes over it, coplanar ground beside it), and each cut is put back as a transmission line
of the cut's length with that gamma and Zc:

    S = S_xx + S_xc L (I - S_cc L)^-1 S_cx       x: the board's ports, c: the cut ports, L: the lines

What it leaves out is the coupling of a cut stretch to anything but its own line: a cut is made only where no
other signal copper (any layer) comes within `keep_out` of it, and not through vias, parts or ports. Reported as
its own solver, 'mixed' (mom25d + lines), next to mom25d's.
"""
import time

import numpy as np

from . import Solver, Result, Option
from .mom import MoM25D, _metal, G as MOM_G
from .. import stop

G = 'mixed (mom25d + lines)'
CUT_MIN = 3.0           # mm: the shortest stretch worth cutting out


def plan_cuts(model, data, dmax, fmin, cut_min=CUT_MIN * 1e-3, keep_out=None, log=print):
    """The stretches of trace to cut out: [(Run of the cut, the run it is in)]. model: mom25d BoardModel; data: its
    board dict (nets per polygon); dmax (m); fmin (Hz): the wave ports on the cut faces need their feed."""
    from shapely.geometry import Point, LineString, shape
    from shapely.ops import unary_union
    from mom25d.line import find_runs, Run
    from mom25d.model import KNOWN_SPAN
    MM = 1e-3
    if model.signal is None:
        log("mixed: the board names no ground net - no signal trace to cut")
        return []
    gnets = set(data.get('ground_nets') or [])
    sig_k = {}
    for lay, ps in data.get('copper', {}).items():
        k = model.iface.get(lay)
        if k is not None:
            g = [shape(p['polygon']) for p in ps if p.get('net') not in gnets]
            if g:
                from shapely import affinity
                sig_k[k] = affinity.scale(unary_union(g), MM, MM, origin=(0, 0)).buffer(0)
    geoms = {k: unary_union(gs) for k, gs in model.shapes.items()}
    runs = find_runs(geoms, {k: dmax for k in geoms}, signal=model.signal, w_max=3e-3, min_len=1.0, margin=1.0)
    H = float(model.stack.z[-1])
    vias = [Point(v.x, v.y) for v in model.vias]
    ports = [Point(*sp['c']) for sp in model.port_specs if 'c' in sp] + \
            [Point(*leg['c']) for sp in model.port_specs if 'legs' in sp for leg in sp['legs'].values()]
    parts = [el.lp.line(0.0) for el in model.lumped] if model.lumped else []
    cuts = []
    for r in runs:
        w = 2 * r.a
        win, need0 = model._wave_window(w, 1.0, dmax)
        er = model.stack.layers[r.k - 1].er
        beta = 2 * np.pi * fmin * np.sqrt(0.5 * (er + 1)) / 299792458.0
        need = max(need0, 1.0 - (win[2] - win[1]) + KNOWN_SPAN / beta)        # the feed each cut face needs
        xc0, xc1 = r.x0 + need, r.x1 - need
        if xc1 - xc0 < cut_min:
            continue
        cut = Run(r.k, r.c0, r.t, r.a, xc0, xc1)
        rect = cut.polygon()
        h = model.stack.layers[r.k - 1].h                    # the dielectric under the trace
        d = keep_out or max(3 * w, 4 * h, 0.5e-3)            # no other signal copper this close
        near = rect.buffer(d)
        if any(rect.buffer(max(2 * w, 3 * h)).contains(p) for p in vias) or any(near.contains(p) for p in ports):
            continue
        if any(near.intersects(LineString(l)) for l in parts):
            continue
        own = Run(r.k, r.c0, r.t, r.a * (1 + 1e-6), xc0 - 2 * d, xc1 + 2 * d).polygon(pad=1e-7)
        crowded = False
        for k, g in sig_k.items():
            other = g.difference(own) if k == r.k else g
            if other.intersects(near):
                crowded = True
                break
        if crowded:
            continue
        cuts.append((cut, r))
    return cuts


def connect(S, cut_idx, lines):
    """The board's ports' S from the network S (n x n) whose ports cut_idx = [(a, b), ...] are joined in pairs by
    two-ports lines = [S_line (2 x 2), ...] (same reference impedance on each side of each joint)."""
    n = S.shape[0]
    c = [i for ab in cut_idx for i in ab]
    x = [i for i in range(n) if i not in c]
    L = np.zeros((len(c), len(c)), complex)
    for j, Sl in enumerate(lines):
        L[2 * j:2 * j + 2, 2 * j:2 * j + 2] = Sl
    Sxx, Sxc, Scx, Scc = S[np.ix_(x, x)], S[np.ix_(x, c)], S[np.ix_(c, x)], S[np.ix_(c, c)]
    return Sxx + Sxc @ L @ np.linalg.solve(np.eye(len(c)) - Scc @ L, Scx)


def line_S(gamma, zc, length, z0):
    """S of a uniform line (gamma 1/m, Zc ohm, length m) referenced to z0 at both ends."""
    gl = gamma * length
    sh, ch = np.sinh(gl), np.cosh(gl)
    den = (zc * zc + z0 * z0) * sh + 2 * zc * z0 * ch
    s11 = (zc * zc - z0 * z0) * sh / den
    s21 = 2 * zc * z0 / den
    return np.array([[s11, s21], [s21, s11]])


def solve_mixed(m, freq, dmax=0.3, sigma=None, max_solves=60, cut_min=CUT_MIN, keep_out=None, log=print,
                progress=None, on_step=None, port='auto', **mom_kw):
    """S over the board's ports (kicad2ems order, as mom.solve returns them), with the long straight runs cut out of
    mom25d's model and put back as lines. mom_kw: the mom25d mesh options (quads, zone, lines, signal_vias, ...)."""
    from mom25d.board import BoardModel
    from mom25d.geo import LinePort
    from mom25d.run import solve_band, _zc
    from mom25d.api import ZONE_OUT, LINE_STEP
    from ..sparams import mixed_mode
    t0 = time.time()
    data = m.to_dict()
    freq = np.asarray(freq, float)
    fmin, fmax = float(freq[0]), float(freq[-1])
    dm = dmax * 1e-3
    metal, t_mm = _metal(m, sigma)
    model = BoardModel(data, port='via' if port == 'lumped' else port, log=lambda *a: None)
    cuts = plan_cuts(model, data, dm, fmin, cut_min=cut_min * 1e-3, keep_out=keep_out and keep_out * 1e-3, log=log)
    removed = 0.0
    cut_ports = []
    for j, (c, r) in enumerate(cuts):
        rect = c.polygon(pad=1e-7)
        model.shapes[c.k] = [model.shapes[c.k][0].difference(rect).buffer(0)]
        if model.signal is not None:
            model.signal = model.signal.difference(rect).buffer(0)
        removed += c.x1 - c.x0
        for end, (x, d) in enumerate(((c.x0, -c.t), (c.x1, c.t))):
            face = c.c0 + c.t * x
            L = (x - r.x0) if end == 0 else (r.x1 - x)
            win, _ = model._wave_window(2 * c.a, L, dm)
            g0, s0, s1, pstep = win
            cut_ports.append(LinePort(c.k, face + d * g0, d, 2 * c.a, -g0, s0=s0 - g0, s1=s1 - g0, step=pstep,
                                      zc=50.0, name=f"cut{j + 1}{'ab'[end]}"))
    if cuts:
        log(f"mixed: {len(cuts)} stretch(es) of trace as lines, {removed * 1e3:.1f} mm in all: " +
            ", ".join(f"M{c.k} w={2 * c.a * 1e3:.3f} mm {1e3 * (c.x1 - c.x0):.1f} mm" for c, _ in cuts))
    else:
        log("mixed: no long, lone straight run to cut - this is mom25d alone")
    base_ports = model.ports

    def ports(**kw):
        return base_ports(**kw) + cut_ports
    model.ports = ports
    planes = {k: 3 * dm for k, gs in model.shapes.items() if sum(g.area for g in gs) > 0.5 * model.outline_area}
    zone, lines = mom_kw.pop('zone', None), mom_kw.pop('lines', None)
    sim = model.simulation(dmax=dm, dmax_layer=planes, fmin=fmin, fmax=fmax, solver='auto', verbose=False,
                           wave_down_to=fmin, metal=metal, **mom_kw,
                           **(dict(zone=zone, dmax_out=ZONE_OUT * dm) if zone else {}),
                           **(dict(line_basis=lines, line_step=LINE_STEP) if lines and lines != 'off' else {}))
    names = [p.name for p in sim.ports]
    zref = dict(model.port_z0)
    for p in cut_ports:
        zref[p.name] = float(p.zc)
    f, Sn, info = solve_band(sim, fmin, fmax, points=len(freq), zref=zref, max_solves=max_solves, log=log,
                             progress=progress, on_step=on_step)
    pairs = [(names.index(f"cut{j + 1}a"), names.index(f"cut{j + 1}b")) for j in range(len(cuts))]
    S = []
    for i, fr in enumerate(f):
        Ls = []
        for j, (c, r) in enumerate(cuts):
            pa = cut_ports[2 * j]
            Ls.append(line_S(pa.gamma_fn(fr), pa.zc_fn(fr), c.x1 - c.x0, pa.zc))
        S.append(connect(Sn[i], pairs, Ls) if cuts else Sn[i])
    S = np.array(S)
    keep = [n for n in names if not n.startswith('cut')]
    if len(f) != len(freq) or not np.allclose(f, freq):
        S = np.stack([np.stack([np.interp(freq, f, S[:, i, j].real) + 1j * np.interp(freq, f, S[:, i, j].imag)
                                for j in range(S.shape[2])], -1) for i in range(S.shape[1])], 1)
    order = [keep.index(p.name) for p in m.ports]
    S, out_names, out_zref = mixed_mode(S[:, order][:, :, order], m)
    unknowns = int(sim.qT.shape[1]) if getattr(sim, 'qT', None) is not None else int(sim.Ne + sim.Nv)
    info = dict(seconds=time.time() - t0, solves=int(info['solves']), unknowns=unknowns, cuts=len(cuts),
                cut_mm=round(removed * 1e3, 3), notes=list(model.notes) + list(sim.log.notes),
                samples_ghz=[round(x / 1e9, 6) for x in sorted(info['solved'])])
    return S, out_names, out_zref, info


class Mixed(MoM25D):
    name, aliases, label = 'mixed', ('segmented',), 'mixed (mom25d + lines)'
    in_all = False                  # --solvers all: a variant of mom25d, named on its own
    capabilities = dict(MoM25D.capabilities, ports='yes: as mom25d',
                        diff_pairs='yes: as mom25d (coupled stretches are not cut)',
                        radiation='approximated: as mom25d, except from the stretches put back as lines')
    options = [
        Option('mixed_cut', CUT_MIN, "mixed: the shortest stretch of straight trace put back as a line, mm (default "
               "{default}); its mom25d mesh options are mom25d's (--mom-dmax, --mom-zone, ...)", float, name='cut',
               gui='Shortest stretch cut out (mm)', group=G, cli=dict(metavar='MM')),
    ]

    def solve(self, run, prep):
        s = run.s
        self._held = {}
        S, names, zref, info = solve_mixed(
            run.m, run.freq, dmax=s.mom_dmax, sigma=s.sigma, max_solves=s.mom_solves, cut_min=s.mixed_cut, log=run.log,
            port=s.mom_port,
            quads={'off': False, 'on': True}.get(s.mom_quads, s.mom_quads),
            zone='auto' if s.mom_zone == 'on' else None, lines=s.mom_lines,
            signal_vias=s.mom_signal_vias, stitching_vias=s.mom_stitching_vias,
            progress=lambda k, n, f: (run.status('mixed', dict(fraction=k / n, solves=k, f_ghz=f / 1e9)), stop.check()))
        info.update(kpi=f"{info['unknowns']:,} unknowns, {info['cuts']} line(s), {info['seconds']:.0f} s",
                    about=f"mixed: mom25d with {info['cuts']} long straight stretch(es) of trace ({info['cut_mm']:g} mm) "
                          f"cut out and put back as transmission lines (gamma and Zc from each line's cross-section): "
                          f"{info['unknowns']:,} unknowns, {info['solves']} EM solves in {info['seconds']:.0f} s. The cut "
                          f"stretches couple to nothing but their own line.")
        run.log(f"mixed: {info['unknowns']:,} unknowns, {info['cuts']} line(s), {info['solves']} EM solves, "
                f"{info['seconds']:.0f} s")
        return Result(S, names, zref, info['seconds'], info=info, comment=f"mixed: mom25d + lines ({run.name})")

    def outputs(self, run, prep, res):
        return None

    def estimate(self, run):
        return None
