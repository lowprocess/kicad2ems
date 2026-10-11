"""
kicad2ems - build, mesh and run openEMS (3D FDTD) models of KiCad boards.

    from kicad2ems import KiCadBoard, Model, resolve_bc, auto_mesh
    m = Model(KiCadBoard('board.kicad_pcb'), nets=['RF', 'GND'], pos_file='board-top.pos')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 6e9, bc=bc)

Modules
    kicad/        .kicad_pcb reader (sexpr, shapes, board, nets, pnp, outline)
    geometry      polygon helpers (y flip, hole splitting for CSXCAD)
    model         3D model: stack, copper, vias, lumped ports (vertical / gap), region cropping
    parts         lumped R / L / C parts from two-pad footprints
    boundaries    per-side boundary conditions, cavity-resonance check
    mesh          automatic rectilinear mesh
    edges         edge walls: PEC where the ground planes end at chosen board sides
    converge      mesh convergence levels
    simulation    openEMS / CSXCAD setup and runs (needs openEMS)
    progress      openEMS output redirection, progress / ETA
    dielectric    wideband (Djordjevic-Sarkar / Debye) dielectric model
    antenna       antenna mode: NF2FF, gain, efficiency, patterns
    dumps         field dumps
    tlines        transmission-line sections: cross-sections and closed-form Z0 per trace / pad / via,
                  coupled sections and discontinuities
    strips        copper drawn as rectilinear shapes turned into line strips
    coupled       coupled microstrip (even / odd mode) and multiconductor line blocks
    fastsolve     the sections as a circuit: S-parameters in milliseconds (scikit-rf or qucsator)
    sparams       mixed-mode conversion, reciprocity fill
    touchstone    .sNp read / write
    plots         figures: model + mesh preview, line sections, S-parameters, convergence, antenna
    report        HTML report, rendered from the run summary (<name>_openems.json)
    events        run events as JSON lines (--events)
    config        Settings: defaults <- TOML <- command line
    pipeline      Job: the stages of a run
    cli           command line (kicad2ems / python -m kicad2ems)

Documentation: docs/index.md in the repository.
"""
__version__ = '0.2.0'

from .importers.kicad import KiCadBoard, read_pos
from .model import Model, Port
from .solvers.openems.boundaries import resolve_bc, cavity_check
from .solvers.openems.mesh import auto_mesh, smooth_lines
from .sparams import mixed_mode
from .touchstone import write_touchstone, read_touchstone
