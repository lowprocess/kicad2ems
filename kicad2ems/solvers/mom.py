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
from . import Solver, Result

G = 'mom25d'


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
