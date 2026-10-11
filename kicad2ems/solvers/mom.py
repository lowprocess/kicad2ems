"""
mom25d as a solver plugin: the board of the run (Board.to_dict(), the content of <name>_board.json) solved by
the 2.5D Method-of-Moments solver mom25d - the package next to this one, which reads that format itself.

    S, names, zref, info = solve(board, freq, dmax=0.3)

MoM assumes laterally infinite dielectrics over an infinite ground plane: the board outline does not exist
for it, and edge walls are rows of vias.
"""
import os
import numpy as np

from ..options import Option
from . import Solver, Result, Deviation

G = 'mom25d'
# --solvers auto (docs/architecture.md#choosing-solvers): seconds per EM solve on 24 cores, fitted on the examples
# run alone: T0 per pair of metal interfaces (the layered-medium tables of each frequency), plus the dense fill
# (DENSE_N2 N^2, up to 3.5k unknowns) or the direct solver's LU (LU_N3 N^3), plus VIA2 per pair of vias (the
# via-via kernels: sma_launch's 185 vias cost 26 s a solve); a sweep takes about SOLVES solves (11..22 on the
# examples). demo_transition: 12.1 s a solve measured, 12.4 s estimated; sma_launch 33.7 s / 33.7 s.
T0, DENSE_N2, LU_N3, VIA2, SOLVES, REF_CORES = 1.0, 1.6e-7, 3.7e-12, 7.6e-4, 16, 24


def mom25d_status():
    """(usable, one-line description) of mom25d for --check and the solver checks before a run: where it
    comes from, its version and whether its compiled core is built (the NumPy fallback is ~5x slower)."""
    try:
        import mom25d
        import triangle                                             # noqa: F401  (mom25d's default mesher)
    except Exception as e:                                          # ImportError, or a broken dependency
        return False, f"{type(e).__name__}: {e}"
    try:
        from mom25d import _core                                    # noqa: F401
        core = 'compiled core'
    except ImportError:
        core = "no compiled core (NumPy, ~5x slower: python -m mom25d.core.build)"
    return True, f"{mom25d.__version__} ({os.path.dirname(mom25d.__file__)}), {core}"


def _metal(m, sigma):
    """mom25d's conductor loss for --sigma: the conductivity with the copper thickness of the ports' layer
    (openEMS models the same copper as conducting sheets of that conductivity). None without sigma."""
    if not sigma:
        return None, None
    from mom25d.sim import Metal
    lays = [p.layer for p in m.ports if p.layer in m.cu_t] or [l for l in m.cu_order if l in m.copper]
    t_mm = m.cu_t.get(lays[0], 0.035) if lays else 0.035
    return Metal(float(sigma), t_mm * 1e-3), t_mm


def solve(m, freq, dmax=0.3, port='auto', mesher='triangle', max_solves=60, sigma=None, log=print, progress=None):
    """S-parameters of the board m on `freq` (Hz) from mom25d, as kicad2ems reports them: (S (nf, n, n) in
    m.user_ports order with differential pairs as (d, c) modes, names, reference impedances, info dict).
    dmax in mm (planes 3x, refined near the copper of other layers). sigma (S/m): conductor loss, as --sigma
    gives openEMS (default: lossless copper, like openEMS's PEC sheets). progress(solves, at most, f)."""
    from mom25d.api import solve_board
    from ..sparams import mixed_mode
    kind = 'via' if port == 'lumped' else port
    pairs = any(len(legs) == 2 for _, legs in m.user_ports) and kind != 'via'
    metal, t_mm = _metal(m, sigma)
    if metal is not None:
        log(f"  mom25d: conductor loss sigma {sigma:g} S/m, {t_mm * 1e3:g} um copper")
    res = solve_board(m.to_dict(), freq, dmax=dmax * 1e-3, port=kind, pairs=pairs, metal=metal, max_solves=max_solves,
                      mesher='gmsh' if mesher == 'gmsh' else 'auto', log=lambda t: log(f"  mom25d:{t}") if 'AFS' in t else None,
                      progress=progress)
    for line in res.model.describe().splitlines()[1:] + list(res.sim.log.notes):
        log(f"  mom25d: {line}")
    log(f"mom25d: {res.unknowns:,} unknowns, solver {res.solver}, ports " +
        ", ".join(f"{p['name']} ({p['kind']})" for p in res.ports))
    S = res.S
    if pairs:                                                       # mom25d's modal ports -> kicad2ems order
        _, names, zref = mixed_mode(np.zeros((1, len(m.ports), len(m.ports))), m)
        idx = [res.names.index(n) for n in names]
        S = S[:, idx][:, :, idx]
    else:
        order = [res.names.index(p.name) for p in m.ports]
        S, names, zref = mixed_mode(S[:, order][:, :, order], m)
    info = dict(seconds=res.seconds, solves=res.solves, unknowns=res.unknowns, triangles=res.triangles,
                solver=res.solver, mesher=mesher, dmax_mm=dmax, ports=res.ports, notes=res.notes)
    return S, names, zref, info


def _solid_ground(m):
    """The board's ground layer, else the lowest copper layer covering half the outline (mom25d's choice), or
    None."""
    if m.ground:
        return m.ground
    half = 0.5 * m.outline.area
    return next((l for l in m.cu_order if sum(p.area for p in m.copper.get(l, [])) >= half), None)


class MoM25D(Solver):
    name, aliases, label = 'mom25d', ('mom',), 'mom25d (MoM)'
    consumes, previewed, rank = 'board', False, 1
    capabilities = dict(ports='yes: lumped, or de-embedded wave ports on a long enough feed', gap_ports='no',
                        diff_pairs='yes', parts='yes: one series element across the gap',
                        vias='yes: over the layers with metal at the via',
                        outline='ignored: laterally infinite dielectric', planes='yes: meshed (slow on plane-heavy boards)',
                        edge_walls='approximated: via rows every 0.5 mm', thickness='ignored: sheets',
                        conductor_loss='yes', dispersion='no: constant tan d', radiation='yes', cavity='no',
                        antenna='no: it solves copper over an infinite ground plane')
    options = [
        Option('mom_dmax', 0.3, "largest MoM cell along the copper, mm (default {default}; planes 3x, refined near "
               "traces)", float, name='dmax', group=G, cli=dict(metavar='MM')),
        Option('mom_port', 'auto', "auto (default): de-embedded wave ports where a straight feed allows, else lumped",
               choices=('auto', 'wave', 'lumped'), name='port', group=G),
        Option('mom_mesher', 'triangle', "MoM surface mesher (default triangle)", choices=('triangle', 'gmsh'),
               name='mesher', gui='MoM mesher', group=G),
        Option('mom_solves', 60, "most EM solves of the adaptive sweep (default {default})", int, name='solves',
               group=G, cli=dict(metavar='N')),
    ]

    def check(self, s=None):
        return mom25d_status()

    def assess(self, run):
        m, out = run.m, []
        if run.s.antenna:
            out.append("antenna mode: it solves copper over an infinite ground plane")
        gaps = [p.name for p in m.ports if p.kind == 'gap']
        if gaps:
            out.append(f"gap port(s) {', '.join(gaps)}: no planar equivalent")
        if _solid_ground(m) is None:
            out.append("no solid ground plane (no copper layer covers half the board): set ground = <layer>")
        return out

    # ---- what it changes, and what it holds -------------------------------------------------------------
    WALL_PITCH, WALL_R = 0.5, 0.05                                  # mm: edge walls as via rows (mom25d.board)

    def deviations(self, run, prep):
        """What mom25d's planar model leaves out or changes, by the rules it states (mom25d.board): the ground is
        an infinite plane, copper below it and the outline are left out, a via spans only the layers with metal
        at it, edge walls are via rows, a part is one element across its gap bridged by a strip."""
        from shapely.geometry import Point, box
        from shapely.ops import unary_union
        m, out = run.m, []
        g = _solid_ground(m)
        if g is None:
            return out
        zg = m.cu_z[g]
        out.append(Deviation('outline', 'outline', 'ignored: laterally infinite dielectric'))
        out.append(Deviation(f"copper:{g}", 'planes', 'the infinite ground plane (not meshed)', dropped=True))
        out += [Deviation(f"copper:{l}", 'planes', 'below the ground: left out', dropped=True)
                for l in m.copper if m.cu_z[l] < zg - 1e-9]
        metal = {l: unary_union(m.copper[l]).buffer(1e-6) for l in m.copper if m.cu_z[l] > zg + 1e-9}

        def span(x, y, z0, z1):
            zs = [zg] * (z0 - 1e-9 <= zg <= z1 + 1e-9) + [m.cu_z[l] for l, cu in metal.items()
                                                       if z0 - 1e-9 <= m.cu_z[l] <= z1 + 1e-9 and cu.contains(Point(x, y))]
            return (min(zs), max(zs)) if len(set(zs)) >= 2 else None
        for i, (x, y, r, z0, z1) in enumerate(m.vias):
            s = span(x, y, z0, z1)
            if s is None:
                out.append(Deviation(f"via:{i}", 'vias', 'left out: metal on fewer than two layers at it', dropped=True))
            elif abs(s[0] - z0) > 1e-9 or abs(s[1] - z1) > 1e-9:
                out.append(Deviation(f"via:{i}", 'vias', 'spans only the layers with metal at it', to=[x, y, r, *s]))
        for i, (axis, c, t0, t1, z0, z1) in enumerate(m.walls):
            n = max(2, int(round((t1 - t0) / self.WALL_PITCH)) + 1)
            pts = [(c, t) if axis == 'x' else (t, c) for t in np.linspace(t0, t1, n)]
            add = [[x, y, self.WALL_R, *s] for x, y in pts for s in [span(x, y, z0, z1)] if s]
            out.append(Deviation(f"wall:{i}", 'edge_walls', f"a row of {len(add)} vias every {self.WALL_PITCH:g} mm",
                                 dropped=True, added=add))
        for p in m.parts:
            if p.layer not in metal:
                out.append(Deviation(f"part:{p.ref}", 'parts', f"on {p.layer}, not above the ground: left out", dropped=True))
                continue
            before = unary_union(m.copper[p.layer])
            strip = unary_union([before, box(*p.s[:2], *p.e[:2])]).area - before.area
            short = [v for v in (p.R, p.L, p.C) if v is not None] == [0.0] and p.C is None
            out.append(Deviation(f"part:{p.ref}", 'parts', 'a strip alone (0 ohm)' if short else
                                 'one series element across the gap, bridged by a strip', to=0 if short else 1,
                                 layer=p.layer, area=strip))
        return out

    def readback(self, run, prep):
        """mom25d's planar model of the board (mom25d.board.BoardModel), in board terms."""
        from mom25d.board import BoardModel
        bm = BoardModel(run.m.to_dict(), log=lambda *a: None)
        zk, lay = bm._zk, {k: l for l, k in bm.iface.items()}
        ports = [leg['name'] for sp in bm.port_specs for leg in (sp['legs'].values() if 'legs' in sp else [sp])]
        return dict(vias=[[v.x * 1e3, v.y * 1e3, v.a * 1e3, float(zk[v.k_bot]), float(zk[v.k_top])] for v in bm.vias],
                    copper={lay[k]: sum(g.area for g in gs) * 1e6 for k, gs in bm.shapes.items() if k in lay},
                    parts={e.name: 1 for e in bm.lumped}, ports=ports)

    def estimate(self, run):
        """The unknowns of mom25d's mesh of the board (meshed here, the ports not characterised) and the time of
        a sweep of up to SOLVES solves."""
        from mom25d.board import BoardModel
        s = run.s
        bm = BoardModel(run.m.to_dict(), log=lambda *a: None)
        dmax = s.mom_dmax * 1e-3
        planes = {k: 3 * dmax for k, gs in bm.shapes.items() if sum(g.area for g in gs) > 0.5 * bm.outline_area}
        sim = bm.simulation(dmax=dmax, dmax_layer=planes, fmin=run.fmin, fmax=run.fmax, characterize=False,
                            verbose=False, wave_down_to=run.fmin, mesh_only=True)
        n, solves, layers = int(sim.Ne + sim.Nv), min(s.mom_solves, SOLVES), len(bm.shapes)
        per = T0 * layers * (layers + 1) / 2 + (DENSE_N2 * n ** 2 if n <= 3500 else LU_N3 * n ** 3) + \
            VIA2 * len(bm.vias) ** 2
        return dict(seconds=solves * per * REF_CORES / (os.cpu_count() or 1), gb=(18 * n ** 2 + 0.9e9) / 1e9,
                    what=f"{n:,} unknowns, ~{solves} EM solves")

    def prepare(self, run):
        what = mom25d_status()[1]
        if 'no compiled core' in what:                              # ~5x slower: say how to build it
            run.log(f"note: mom25d {what}")

    def solve(self, run, prep):
        s = run.s
        S, names, zref, info = solve(run.m, run.freq, dmax=s.mom_dmax, port=s.mom_port, mesher=s.mom_mesher,
                                     max_solves=s.mom_solves, sigma=s.sigma, log=run.log,
                                     progress=lambda k, n, f: run.status('mom25d', dict(fraction=k / n, solves=k,
                                                                                        f_ghz=f / 1e9)))
        kinds = ", ".join(f"{p['name']} {p['kind']}" for p in info['ports'])
        info.update(kpi=f"{info['unknowns']:,} unknowns, {info['seconds']:.0f} s",
                    about=f"mom25d, a 2.5D Method-of-Moments solver: {info['unknowns']:,} unknowns ({info['triangles']:,} "
                          f"triangles, {info['mesher']} mesher, cells up to {info['dmax_mm']:g} mm), {info['solves']} EM "
                          f"solves of an adaptive sweep in {info['seconds']:.0f} s; ports: {kinds}. MoM sees laterally "
                          f"infinite dielectrics over an infinite ground plane: no board edge, no air box, no box "
                          f"resonances; radiation and surface waves are in.")
        run.log(f"mom25d: {info['solves']} EM solves, {info['seconds']:.0f} s")
        return Result(S, names, zref, info['seconds'], info=info, comment=f"mom25d 2.5D MoM ({run.name})")
