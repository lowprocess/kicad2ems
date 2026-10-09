"""
kicad2openems - build, mesh and run openEMS (3D FDTD) models of KiCad boards.

    from kicad2openems import KiCadBoard, Model, resolve_bc, auto_mesh
    m = Model(KiCadBoard('board.kicad_pcb'), nets=['RF', 'GND'], pos_file='board-top.pos')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 6e9, bc=bc)

Modules
    kicad/        .kicad_pcb reader (sexpr, shapes, board, nets, pnp, outline)
    geometry      polygon helpers (y flip, hole splitting for CSXCAD)
    model         3D model: stack, copper, vias, lumped ports (vertical / gap)
    parts         lumped R / L / C parts from two-pad footprints
    boundaries    per-side boundary conditions, cavity-resonance check
    mesh          automatic rectilinear mesh
    converge      mesh convergence levels
    simulation    openEMS / CSXCAD setup and runs (needs openEMS)
    progress      openEMS output redirection, progress / ETA
    dielectric    wideband (Djordjevic-Sarkar / Debye) dielectric model
    antenna       antenna mode: NF2FF, gain, efficiency, patterns
    dumps         field dumps
    sparams       mixed-mode conversion, reciprocity fill
    touchstone    .sNp read / write
    plots         model + mesh preview, S-parameter, convergence, antenna plots
    report        HTML report
    config        TOML run configuration
    pipeline      Job: the stages of a run
    cli           command line (kicad2openems / python -m kicad2openems)

Documentation: docs/index.md in the repository.
"""
__version__ = '0.1.0'

from .kicad import KiCadBoard, read_pos
from .model import Model, Port
from .boundaries import resolve_bc, cavity_check
from .mesh import auto_mesh, smooth_lines
from .sparams import mixed_mode
from .touchstone import write_touchstone, read_touchstone
