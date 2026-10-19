"""
kicad2ems - KiCad (or Gerber) boards as electromagnetic simulations: one canonical board, solved by
openEMS (3D FDTD), mom25d (2.5D MoM) and a transmission-line model.

    from kicad2ems import KiCadBoard, Board, resolve_bc, auto_mesh
    b = Board(KiCadBoard('board.kicad_pcb'), nets=['RF', 'GND'], pos_file='board-top.pos')
    b.save('board.json')                        # the board every solver gets
    bc = resolve_bc('auto', b)
    mesh = auto_mesh(b, 1e9, 6e9, bc=bc)        # openEMS's mesh of it

Modules
    importers/    board readers: kicad/ (.kicad_pcb: sexpr, shapes, board, nets, pnp, outline), gerber.py
    board         the canonical board: stack, copper with nets, vias, lumped ports (vertical / gap), parts, edge
                  walls, primitives; board.json
    region        region cropping, nets = auto, extra ports, cut ports
    geometry      polygon helpers (y flip, hole splitting for CSXCAD)
    parts         lumped R / L / C parts from two-pad footprints
    edges         edge walls where the ground planes end at chosen board sides
    options       Option (one setting, declared once) and the value converters
    solvers/      the solver plugins: Solver, Result, the registry (entry points 'kicad2ems.solvers'),
                  capabilities
      openems/      openEMS (3D FDTD): plugin, boundaries, mesh, converge, simulation, progress, dielectric,
                    antenna, dumps, fields
      mom           mom25d (2.5D MoM): the board solved through mom25d.api.solve_board
      lines/        the line model: tlines (sections, Z0, coupled sections, discontinuities), strips, coupled,
                    fastsolve (the sections as a circuit); plugin (qucs, scikit)
    sparams       mixed-mode conversion, reciprocity fill
    touchstone    .sNp read / write
    plots         figures: model / mesh preview, line sections, S-parameters, convergence, antenna
    report        HTML report, rendered from the run summary (<name>_run.json)
    events        run events as JSON lines (--events)
    config        Settings (made from the option tables): defaults <- TOML <- command line
    pipeline      Job: the stages of a run, over the solver plugins
    cli           command line (kicad2ems / python -m kicad2ems)

Documentation: docs/index.md in the repository.
"""
__version__ = '261019-r4'

from .importers.kicad import KiCadBoard, read_pos
from .board import Board, Port
from .solvers.openems.boundaries import resolve_bc, cavity_check
from .solvers.openems.mesh import auto_mesh, smooth_lines
from .sparams import mixed_mode
from .touchstone import write_touchstone, read_touchstone
