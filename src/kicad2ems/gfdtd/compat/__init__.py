"""
openEMS's and CSXCAD's Python packages over gfdtd, for scripts written for
openEMS to run here unchanged: put PATH first on the module path and their
`from CSXCAD import ContinuousStructure`, `from openEMS import openEMS`,
`from openEMS.physical_constants import *` (and CSXCAD.CSXCAD,
CSXCAD.CSRectGrid, openEMS.openEMS, openEMS.automesh, openEMS.ports) are
gfdtd's:

    PYTHONPATH=$(python -m kicad2ems.gfdtd.compat) python Simple_Patch_Antenna.py

or in Python: sys.path.insert(0, kicad2ems.gfdtd.compat.PATH). It shadows a
real CSXCAD / openEMS installed on the same path: use it in an environment of
its own, or only where openEMS is not installed. What gfdtd's interface covers
and what it refuses: docs/gfdtd.md.
"""
import os

PATH = os.path.dirname(os.path.abspath(__file__))
