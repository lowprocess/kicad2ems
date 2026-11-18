"""
kicad2ems - KiCad (or Gerber) boards as electromagnetic, power-integrity,
circuit and thermal simulations. One package, its parts subpackages (each was a
repository of its own until 2026-11-16):

    core      the board model, the settings, the pipeline, the line model, the
              registries of the solvers and analyses, the command line
    gui       the window (PySide6)
    fdtd      the FDTD solvers' shared model: openEMS and gfdtd
    gfdtd     a 3D FDTD engine on the GPU or a compiled CPU core
    mom25d    a 2.5D Method-of-Moments solver, and the mixed solver
    pcbqs     power integrity, quasistatic fields, operational circuits,
              temperature
    fem       Palace and Elmer behind the same questions
    dfsynth   distributed-element filter synthesis
    ipc       the KiCad plugin's window and its IPC link
    emsdev    the machine: GPUs, CPU threads, memory, the compiled cores' builds
    examples  the example boards and their configs (the checkout's examples/)

    from kicad2ems.core import KiCadBoard, Board

Documentation: docs/index.md in the repository.
"""
__version__ = '0.4.0.dev0'

import os as _os
# kicad2ems.examples is the checkout's examples/ (pyproject's package-dir). An
# editable install that puts only src/ on sys.path does not have it there: look
# in the checkout too
_checkout = _os.path.dirname(_os.path.dirname(__path__[0]))
if not _os.path.isdir(_os.path.join(__path__[0], 'examples')) and \
        _os.path.isfile(_os.path.join(_checkout, 'examples', '__init__.py')):
    __path__.append(_checkout)

