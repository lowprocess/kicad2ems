"""
mom25d.geo - wave ports on a straight feed in any in-plane direction (LinePort:
KiCad traces at 45 deg etc.), and the mesher (emsdev.numerics.geo.mesh_polygons)
on mom25d's own compiled core
"""
import numpy as np

from kicad2ems.emsdev.numerics import geo


class LinePort:
    """
    Wave port on a straight feed line.
      layer     : metal interface index
      center    : (x, y) of the gap (delta-gap source) on the feed centre line
      direction : vector pointing INTO the device along the feed (any angle)
      width     : feed width (the gap and sample cuts span this, centred on
                  `center`)
      ref_dist  : distance from the gap to the reference plane, along
                  `direction`
      s0, step  : closest sample cut to the gap, and spacing between cuts
      s1        : farthest sample cut (default = ref_dist). Keep the cuts clear
                  of discontinuities: the reference plane may lie beyond s1
                  (the fitted waves are shifted to it).
      zc        : reference impedance
    """
    kind = 'wave'

    def __init__(self, layer, center, direction, width, ref_dist, s0=None,
                 step=None, zc=50.0, name='', s1=None):
        self.layer, self.s1 = layer, s1
        d = np.asarray(direction, float)
        self.c, self.d = np.asarray(center, float), d / np.linalg.norm(d)
        self.t = np.array([-self.d[1], self.d[0]])
        self.w, self.ref, self.s0, self.step, self.zc, self.name = (width,
                                                                    ref_dist,
                                                                    s0, step,
                                                                    zc, name)

    def line(self, s):
        """End points of the transverse line at distance s from the gap (s=0:
        the gap)."""
        p = self.c + self.d * s
        return p - self.t * self.w / 2, p + self.t * self.w / 2

    @property
    def axis_aligned(self):
        return abs(self.d[0]) < 1e-12 or abs(self.d[1]) < 1e-12


def mesh_polygons(*args, **kw):
    """emsdev.numerics.geo.mesh_polygons on mom25d's compiled core."""
    try:
        from . import _core
    except ImportError:
        _core = None
    if _core is None or not hasattr(_core, 'cdt_triangulate'):
        raise RuntimeError(
            "mom25d meshes with its compiled core, which is not built (or is "
            "older than its mesher): python -m kicad2ems.mom25d.core.build "
            "(needs a C++17 compiler and pybind11)"
        )
    return geo.mesh_polygons(*args, core=_core, **kw)
