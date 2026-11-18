"""
kicad2ems.core - KiCad (or Gerber) boards as electromagnetic simulations: one
canonical board, solved by openEMS (3D FDTD), mom25d (2.5D MoM) and a
transmission-line model.

    from kicad2ems.core import KiCadBoard, Board, resolve_bc, auto_mesh
    b = Board(KiCadBoard('board.kicad_pcb'), nets=['RF', 'GND'],
              pos_file='board-top.pos')
    b.save('board.json')                  # the board every solver gets
    bc = resolve_bc('auto', b)
    mesh = auto_mesh(b, 1e9, 6e9, bc=bc)  # openEMS's mesh of it

Modules
    importers/    board readers: kicad/ (.kicad_pcb: sexpr, shapes, board, nets,
                  pnp, outline), gerber.py
    board         the canonical board: stack, copper with nets, vias, lumped
                  ports (vertical / gap), parts, edge walls, primitives;
                  board.json
    region        region cropping, nets = auto, extra ports, cut ports
    geometry      polygon helpers (y flip, hole splitting for CSXCAD)
    parts         lumped R / L / C parts from two-pad footprints
    edges         edge walls where the ground planes end at chosen board sides
    options       Option (one setting, declared once) and the value converters
    fields/       field results of any solver: dumps, frames (DumpSource),
                  antenna (far-field analysis)
    solvers/      the solvers: Solver, Result, the registry (SOLVERS: every
                  solver), capabilities. The full-wave solvers are parts of
                  their own: openEMS and gfdtd (kicad2ems.fdtd, on its shared
                  FDTD model), mom25d and mixed (kicad2ems.mom25d.solvers),
                  Palace (kicad2ems.fem), pcbqs
      lines/        the line model: tlines (sections, Z0, coupled sections,
                    discontinuities: formulas, reference, builder, topology),
                    strips, coupled, fastsolve (the sections as a circuit);
                    solver (qucs, scikit)
    sparams       mixed-mode conversion, reciprocity fill
    touchstone    .sNp read / write
    plots         figures: model / mesh preview, line sections, S-parameters,
                  convergence, antenna
    report        HTML report, rendered from the run summary (<name>_run.json)
    events        run events as JSON lines (--events)
    config        Settings (made from the option tables): defaults <- TOML <-
                  command line
    pipeline      Job: the stages of a run, over the solvers
    cli           command line (kicad2ems / python -m kicad2ems)
    runs          a run started by another program (kicad2ems-gui, the KiCad
                  plugin): its TOML, events, stopping it

The window is kicad2ems.gui (the gui extra brings its packages).

Documentation: docs/index.md in the repository.
"""
from .. import __version__

# imported when first asked for: the leaf modules pcbqs shares (units,
# constants, touchstone, fields.dumps) do not bring the board model
_NAMES = dict(KiCadBoard='importers.kicad', read_pos='importers.kicad',
              Board='board', Port='board', mixed_mode='sparams',
              write_touchstone='touchstone', read_touchstone='touchstone')


def __getattr__(name):
    if name not in _NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module('.' + _NAMES[name], __name__), name)
