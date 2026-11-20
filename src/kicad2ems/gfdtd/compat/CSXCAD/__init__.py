"""CSXCAD's package over gfdtd (kicad2ems.gfdtd.compat)."""
from kicad2ems.gfdtd.csx import ContinuousStructure  # noqa: F401
from . import CSXCAD, CSRectGrid, Utilities  # noqa: F401

# the viewer a script may start (os.system(AppCSXCAD_BIN ...)): none here
AppCSXCAD_BIN = 'true'
