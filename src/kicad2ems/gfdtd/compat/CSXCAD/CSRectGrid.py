"""CSXCAD.CSRectGrid over gfdtd: the grid, and its coordinate systems (gfdtd's
grid is Cartesian: a cylindrical one is refused)."""
import enum

from kicad2ems.gfdtd.csx import CSRectGrid  # noqa: F401


class CoordinateSystem(enum.IntEnum):
    CARTESIAN = 0
    CYLINDRICAL = 1
