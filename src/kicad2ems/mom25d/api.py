"""
mom25d.api - one call for a program that hands mom25d a board and wants
S-parameters back, without reaching into the Simulation's internals (kicad2ems's
MoM solver is the first user of this seam).

    from kicad2ems.mom25d.api import solve_board
    res = solve_board('out/filter_board.json', np.linspace(1e9, 6e9, 401),
                      dmax=0.3e-3)
    res.S, res.names, res.zref  # (nf, n, n) at the ports' reference impedances,
                                # port names, ohms
    res.ports, res.unknowns, res.solves, res.seconds, res.notes

The board is kicad2ems's file format (see board.BoardModel): the path of a
<name>_board.json, or its content. Fields of Result are the stable part;
Result.model and Result.sim give the objects behind it for anything else (and
change with mom25d).
"""
import json
import os
import time
from dataclasses import dataclass, field

import numpy as np

from .thickness import DEFAULT as THICKNESS


@dataclass
class Result:
    f: np.ndarray                   # Hz: the requested frequencies
    S: np.ndarray                   # (nf, n, n) at zref
    # port names (a differential pair with pairs=True: P1d / P1c modes)
    names: list
    zref: list                      # output reference impedance per port, ohms
    # [dict(name, kind='wave' | 'lumped', zc=line impedance of a wave port)]
    ports: list
    unknowns: int                   # RWG edges + via chains
    triangles: int
    solver: str     # 'direct' or 'blr'
    precision: str  # 'double' or 'mixed'
    mesher: str     # what was asked for ('auto': mesh2d's triangulation)
    solves: int     # EM solves of the sweep
    solved: list    # the frequencies solved (Hz)
    seconds: float  # model, mesh, port characterisation and sweep
    notes: list = field(default_factory=list)
    # the adaptive sweep's history (emsdev.afs.adaptive_sweep), one dict a model
    afs: list = field(default_factory=list)
    afs_tol: float = None                       # its tolerance (max |dS|)
    model: object = None            # the BoardModel
    sim: object = None              # the Simulation


# cells per wavelength the mesh grows to away from the features (0: not by
# the wavelength)
CELLS = 30.0
# ... and to this x dmax at least (cell_growth; 0 or 1: not), where that is
# no more than a wavelength / OPEN (a plane's cells: / PLANE)
COARSEN = 2.0
OPEN, PLANE = 15.0, 8.0
# zone=: cells up to this x dmax outside the zone around the signal copper
ZONE_OUT = 5.0


def solve_board(board, freq, dmax=0.3e-3, plane_factor=3.0, port='auto',
                pairs=False, mesher='auto', metal=None, ground=None,
                max_solves=60, afs_tol=5e-3, log=print, progress=None,
                on_step=None, quads=False, zone=None,
                stitching_vias='auto',
                keep_at=(), quad_tol=35.0, coarsen=COARSEN, planes='pec',
                stitching_pads='drop', wideband=None, cells=CELLS,
                thickness=THICKNESS, edges='fine', excite=None):
    """S-parameters of a board (a <name>_board.json path, or its content) on
    `freq` (Hz). excite: the port names driven (every port terminated in its
    reference; the rest of S by reciprocity, 0 between two undriven ports:
    sim._sparams_terminated), None: every port.

    dmax (m): largest cell along the copper; layers whose metal covers more than
    half the board (planes) get plane_factor x dmax (refined near the copper of
    other layers). port: 'auto' (de-embedded wave ports where the straight feed
    spans enough for the fit down to the lowest frequency, else lumped), 'wave'
    or 'via' (lumped). pairs:
    P<n>+ / P<n>- ports as one mixed-mode port. metal: a sim.Metal (conductor
    loss), or None (lossless). log: lines of progress (the adaptive sweep's);
    progress(solves, max_solves, f): after each EM solve. on_step(entry): after
    each model of the adaptive sweep (its change and prediction error,
    afs.adaptive_sweep). quads: near-rectangular triangle pairs as quad
    cells (True, or 'centre'), quad_tol: the largest deviation of their corners
    from 90 degrees. coarsen: cells grow from dmax at corners, vias and ports to
    coarsen x dmax away from them (geo.mesh_polygons; None: dmax everywhere).
    planes: 'pec' (default: solid planes as the ground and a PEC cover, not
    meshed: mom25d.board) or 'mesh'. stitching_pads: 'drop' (default: a ground
    via's pad with nothing else on its layer left out, the via kept) or 'keep'.
    thickness: one of mom25d.thickness.MODES ('sides', the default: the
    copper's edges stood up as plates; 'off', 'grow', 'edge'), as
    mom25d.thickness.resolve allows it on the board. wideband: None
    (constant er, tand) or the frequency (Hz) the stack's er and tand hold at,
    Djordjevic-Sarkar elsewhere."""
    from .geo import LinePort
    from .run import solve_band
    name = os.path.basename(board) if isinstance(board, str) else 'board'
    freq = np.asarray(freq, float)
    fmin, fmax = float(freq[0]), float(freq[-1])
    t0 = time.time()
    model, sim = build_board(
        board, fmin, fmax, dmax=dmax, plane_factor=plane_factor, port=port,
        pairs=pairs, mesher=mesher, metal=metal, ground=ground, log=log,
        quads=quads, zone=zone, stitching_vias=stitching_vias,
        quad_tol=quad_tol, coarsen=coarsen,
        planes=planes, stitching_pads=stitching_pads, wideband=wideband,
        title=name, cells=cells, thickness=thickness, edges=edges)
    if excite:
        names = [p.name for p in sim.ports]
        miss = [x for x in excite if x not in names]
        if miss:
            raise ValueError(f"excite: no such port(s) {', '.join(miss)}")
        sim.excite = [names.index(x) for x in excite]
    f, S, info = solve_band(sim, fmin, fmax, points=len(freq),
                            zref=model.port_z0, max_solves=max_solves,
                            afs_tol=afs_tol, log=log, progress=progress,
                            on_step=on_step, keep_at=keep_at, freq=freq)
    sim.fast.release_host()  # (the Result keeps sim)
    # the sweep's grid onto the requested one
    if len(f) != len(freq) or not np.allclose(f, freq):
        S = np.apply_along_axis(
            lambda s: np.interp(freq, f, s.real) + 1j * np.interp(
                freq, f, s.imag), 0, S)
    return Result(
        f=freq, S=S, names=list(info['names']), zref=list(info['zref']), ports=[
            dict(name=p.name,
                 kind=('wave' if isinstance(p, LinePort) else 'lumped'),
                 zc=float(getattr(p, 'zc', 0) or 0)) for p in sim.ports
        ], unknowns=(int(sim.qT.shape[1]) if getattr(sim, 'qT', None)
                     is not None else int(sim.Ne + sim.Nv)),
        triangles=int(len(sim.rwg.tris)), solver=sim.solver,
        precision=getattr(sim, 'precision',
                          'double'), mesher=mesher, solves=int(info['solves']),
        solved=list(info['solved']), seconds=time.time() - t0,
        notes=list(model.notes) + list(sim.log.notes), model=model, sim=sim,
        afs=list(info.get('afs', [])), afs_tol=info.get('afs_tol'))


# the mesh across the copper's edges (edges=): fine, the edge rings (ncross 6,
# graded from the narrowest trace) and the rings at narrow gaps; coarse, neither
# (as Momentum without its edge mesh): a CM5 carrier's F.Cu piece (18 nets, 447
# mm of 0.13 mm trace) 28,964 unknowns -> 3,899
EDGES = {'fine': {}, 'coarse': dict(ncross=2, gap_rings=None)}


def edge_kw(edges):
    """Simulation's mesh options for edges= ('fine', 'coarse')."""
    if edges not in EDGES:
        raise ValueError(f"edges: {', '.join(EDGES)}, not {edges!r}")
    return dict(EDGES[edges])


def build_board(board, fmin, fmax, dmax=0.3e-3, plane_factor=3.0, port='auto',
                pairs=False, mesher='auto', metal=None, ground=None, log=print,
                quads=False, zone=None, stitching_vias='auto', quad_tol=35.0,
                coarsen=COARSEN, planes='pec', stitching_pads='drop',
                wideband=None, title=None, mesh_only=False, characterize=True,
                cells=CELLS, thickness=THICKNESS, edges='fine'):
    """(BoardModel, Simulation) of a board as solve_board builds them, with its
    options (solve_board's). mesh_only: stop once the mesh and its unknowns are
    known (a preview of the mesh solve_board would solve on; the quad cells are
    paired only after that, so a mesh_only simulation has none)."""
    from .board import BoardModel
    if isinstance(board, str):
        title = title or os.path.basename(board)
        with open(board, encoding='utf-8') as fh:
            board = json.load(fh)
    model = BoardModel(board, ground=ground, port=port, pairs=pairs,
                       planes=planes, stitching_pads=stitching_pads,
                       wideband=wideband, thickness=thickness, log=log)
    if (metal is not None and metal.on
            and any(isinstance(k, str) for k in metal.on)):
        import copy  # a finish named by copper layer: on its interface
        metal = copy.copy(metal)
        metal.on = {
            model.iface.get(k, k) if isinstance(k, str) else k: v
            for k, v in metal.on.items()
        }
        # layers not modelled: none
        metal.on = {k: v for k, v in metal.on.items() if not isinstance(k, str)}
    layer_dmax = {k: plane_factor * dmax for k, gs in model.shapes.items()
                  if sum(g.area for g in gs) > 0.5 * model.outline_area}
    coarsen = cell_growth(model, layer_dmax, fmax, dmax, cells, coarsen, log)
    sim = model.simulation(
        dmax=dmax, dmax_layer=layer_dmax, fmin=fmin, fmax=fmax, solver='auto',
        verbose=False, wave_down_to=fmin, mesher=mesher, title=title or 'board',
        metal=metal, quads=quads, quad_tol=quad_tol, coarsen=coarsen,
        stitching_vias=stitching_vias, mesh_only=mesh_only,
        characterize=characterize, **edge_kw(edges),
        **(dict(zone=zone, dmax_out=ZONE_OUT * dmax) if zone else {}))
    return model, sim


def cell_growth(model, planes, fmax, dmax, cells=CELLS, coarsen=COARSEN,
                log=None):
    """{layer: x}: how far each layer's cells grow away from the features
    (corners, line ends, vias, ports; the edge rings as they are), x times
    their size there (dmax; a plane's, planes[layer]: the layers whose metal
    covers more than half the board), None where they do not. To the larger
    of two sizes:
    - a wavelength / cells in the densest dielectric at fmax, on a signal
      layer at most 4 x its narrowest conductor (longer cells cannot follow
      its current), on a plane x its cells' factor. As planar MoM tools size
      their cells (30 per wavelength: the current along a line within about
      1 %). On 16 Solver Matrix boards against openEMS: 38 % of dmax's
      unknowns, a quarter of the time, the median RMS 0.059 against 0.057
      (2026-11-15); the same size everywhere, not graded, lost 0.04 on
      resonators and via boards;
    - coarsen x the layer's size, where that is no more than a wavelength /
      OPEN (a plane's: / PLANE). At 8-13 GHz the first is 0.4-0.6 mm, under
      2 x 0.3 mm: the 45 Solver Matrix boards in 10.6 min against 15.2 with
      it, 44 within 0.005 of their RMS against openEMS without it (median
      0.0632 both; 2026-11-18, before the bounds). Past a wavelength / 15
      patch arrays moved (cells of / 9: 0.036 to 0.041), and a slot along
      plane cells of a wavelength / 6.7 by 0.024.
    cells 0: the second alone; coarsen 0 or 1: the first alone."""
    from .sim import Simulation
    er = max(l.er for l in model.stack.layers)
    lam = 299792458.0 / (fmax * er ** 0.5)
    out, said = {}, []
    for k, gs in model.shapes.items():
        own = planes.get(k, dmax)
        ws = [] if k in planes else [
            Simulation._guess_wref(g, []) for g in gs if not g.is_empty]
        by_wl = (min([lam / cells * own / dmax] + [4 * w for w in ws])
                 if cells and cells > 0 else 0.0)
        by_x = (min(coarsen * own, lam / (PLANE if k in planes else OPEN))
                if coarsen and coarsen > 1 else 0.0)
        x = max(by_wl, by_x) / own
        out[k] = x if x > 1.0 else None
        if out[k]:
            said.append(
                f"{'planes ' if k in planes else ''}{own * 1e3:.3g} to "
                f"{x * own * 1e3:.3g} mm (a wavelength / {lam / (x * own):.3g}"
                + (f", {coarsen:g} x" if by_x > by_wl
                   and by_x == coarsen * own else "") + ")")
    if log is not None and said:
        log("  cells grow away from the features, at "
            f"{fmax / 1e9:g} GHz in er {er:g}: "
            + "; ".join(dict.fromkeys(said)))
    return out
