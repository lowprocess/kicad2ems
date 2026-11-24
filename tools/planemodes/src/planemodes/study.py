"""A study: configuration in, one result per plane pair out.

    cfg = StudyConfig(fmin=1e9, fmax=10e9, pairs=[('F.Cu', 'In1.Cu')])
    for r in run_study(board, cfg, progress=print):
        print(r.cavity.name, r.modes.f / 1e9)
"""
import time
from dataclasses import dataclass, field

from .cavity import Cavity, guess_ref_nets, make_cavity, plane_pairs
from .mesh import Mesh, MeshConfig, mesh_cavity
from .solver import Modes, SolverConfig, solve_modes


@dataclass
class StudyConfig:
    fmin: float = 1e9                       # Hz
    fmax: float = 10e9                      # Hz
    ref_nets: list = None                   # ground nets (None: guessed from names)
    pairs: list = None                      # [(top, bottom)] (None: every plane pair)
    min_cover: float = 0.3                  # a layer is a plane above this ground coverage
    hole_fill: float = 2.0                  # mm^2: smaller holes in a plane are filled
    mesh: MeshConfig = field(default_factory=MeshConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)


@dataclass
class PairResult:
    cavity: Cavity
    mesh: Mesh
    modes: Modes
    seconds: dict                           # time spent: cavity, mesh, solve


class Cancelled(Exception):
    pass


def run_study(board, cfg, progress=None, cancelled=None):
    """Solve every requested plane pair. progress(fraction, text) reports
    as it goes; cancelled() returning True stops it (raises Cancelled)."""
    say = progress or (lambda frac, text: None)
    ref = cfg.ref_nets or guess_ref_nets(board)
    if not ref:
        raise ValueError('no ground nets: give StudyConfig.ref_nets')
    pairs = cfg.pairs or plane_pairs(board, ref, cfg.min_cover)
    if not pairs:
        raise ValueError(f'no plane pairs with ground on both layers (nets {ref})')
    out = []
    for i, (top, bottom) in enumerate(pairs):
        step = lambda part, text: say((i + part) / len(pairs), f'{top}/{bottom}: {text}')
        t0 = time.perf_counter()
        step(0.0, 'finding the cavity')
        cav = make_cavity(board, top, bottom, ref, cfg.hole_fill)
        if cav.region.is_empty:
            step(1.0, 'no overlapping ground copper - skipped')
            continue
        t1 = time.perf_counter()
        if cancelled and cancelled():
            raise Cancelled()
        step(0.15, f'meshing ({len(cav.shorts)} ground vias, {len(cav.ports)} other vias)')
        mesh = mesh_cavity(cav, cfg.fmax, cfg.mesh)
        t2 = time.perf_counter()
        if cancelled and cancelled():
            raise Cancelled()
        step(0.4, f'solving {len(mesh.nodes):,} nodes')
        modes = solve_modes(mesh, cav, cfg.fmin, cfg.fmax, cfg.solver)
        t3 = time.perf_counter()
        step(1.0, f'{len(modes.f)} modes in {t3 - t0:.1f} s')
        out.append(PairResult(cav, mesh, modes,
                              dict(cavity=t1 - t0, mesh=t2 - t1, solve=t3 - t2)))
    return out
