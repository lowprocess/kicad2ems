"""
Boundary conditions: per-side selection and the closed-box resonance check.
"""
import numpy as np
from shapely.ops import unary_union
from .constants import C0


SIDES = ('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax')


def bottom_is_plane(m, frac=0.85):
    """True when the lowest copper layer is the bottom of the model (z = 0) and covers the outline
    (>= 85 %: pours stop short of the board edge)."""
    if not m.cu_order or m.cu_z[m.cu_order[0]] != 0.0 or m.cu_order[0] not in m.copper:
        return False
    return unary_union(m.copper[m.cu_order[0]]).area >= frac * m.outline.area


def resolve_bc(spec, m):
    """Boundary per side. 'auto' (default): PEC directly on a solid bottom plane (exact, no air below,
    an infinite ground), MUR elsewhere. Otherwise one value for all sides (MUR, PML_8,
    PEC) or six comma-separated values xmin,xmax,ymin,ymax,zmin,zmax."""
    spec = (spec or 'auto').strip()
    if spec.lower() == 'auto':
        bc = ['MUR'] * 6
        if bottom_is_plane(m):
            bc[4] = 'PEC'
        return bc
    parts = [p.strip().upper() for p in spec.split(',')]
    if len(parts) == 1:
        parts *= 6
    if len(parts) != 6:
        raise ValueError("--bc: one value or six (xmin,xmax,ymin,ymax,zmin,zmax)")
    for p in parts:
        if p not in ('PEC', 'PMC', 'MUR') and not p.startswith('PML_'):
            raise ValueError(f"--bc: unknown boundary {p}")
    if parts[4] == 'PEC' and not bottom_is_plane(m):
        m.notes.append("bottom PEC: the lowest copper layer is not a solid plane - the PEC wall sits "
                       "below the board after the air margin")
    return parts


def cavity_check(m, mesh, bc, fmax, log=print):
    """A box closed by PEC side walls resonates; warn when its lowest mode is below fmax."""
    if not all(b == 'PEC' for b in bc[:4]):
        return None
    Lx = (mesh['x'][-1] - mesh['x'][0]) * 1e-3
    Ly = (mesh['y'][-1] - mesh['y'][0]) * 1e-3
    H = (mesh['z'][-1] - mesh['z'][0]) * 1e-3
    t = m.thickness * 1e-3
    er = sum((l['z1'] - l['z0']) * l['er'] for l in m.layers) / max(m.thickness, 1e-12)
    eeff = H / (t / er + (H - t))                                   # E normal to the board: series fill
    f = C0 / (2 * np.sqrt(eeff)) * np.sqrt(1 / Lx ** 2 + 1 / Ly ** 2)
    if f < fmax:
        log(f"WARNING: PEC side walls make a {Lx * 1e3:.0f} x {Ly * 1e3:.0f} mm cavity with its first resonance near "
            f"{f / 1e9:.1f} GHz (< fmax {fmax / 1e9:.1f} GHz): expect spurious resonances and slow decay. "
            f"Use MUR side walls (default) unless the real board is in a shield can of that size.")
    return f
