"""
3D field views: a volume dump as a translucent voxel cloud over the board (copper, vias, substrate), for the
GUI's 3D field panel (gui/volumetab.py) and its GIF / MP4 export. No Qt here: the arrays the OpenGL items take.

    vol = Volume(src, frame)                     # one frame of a volume dump (solvers.openems.fields.DumpSource)
    v, (origin, step) = vol.scalar('mag', phase)  # uniform grid (mm): |F| or |Re(F e^jphi)| or a component
    rgba = colour(v, lo, hi, log=True)           # (nx, ny, nz, 4) uint8: low values transparent
    tris = copper_triangles(geometry)            # [(layer, z mm, vertices (n, 3), faces (m, 3))]

The openEMS mesh is graded (fine at the copper, coarse in the air): the dump is resampled onto a uniform grid
of about `n` points along its longest side (OpenGL volume textures are uniform), each axis linearly. A
frequency-domain dump animates over the phase, Re(F e^(j phi)): its magnitude shows the standing and travelling
waves; a time-domain one over its frames.
"""
import numpy as np

COMPONENTS = ('mag', 'x', 'y', 'z')


def _resample_axis(a, src, dst, axis):
    """a sampled at the (increasing) coordinates src along `axis`, linearly at dst."""
    i = np.clip(np.searchsorted(src, dst) - 1, 0, len(src) - 2)
    w = np.clip((dst - src[i]) / np.where(src[i + 1] > src[i], src[i + 1] - src[i], 1), 0, 1)
    sh = [1] * a.ndim
    sh[axis] = -1
    w = w.reshape(sh)
    return np.take(a, i, axis=axis) * (1 - w) + np.take(a, i + 1, axis=axis) * w


class Volume:
    """One frame of a volume dump on a uniform grid of about n points along its longest side (z refined by
    zfine: the substrate is thin). Complex for a frequency-domain dump."""

    def __init__(self, src, frame, n=128, zfine=2.0):
        mesh, data = src.load(frame)
        xs = [np.asarray(mesh[a], float) for a in 'xyz']
        if min(len(x) for x in xs) < 2:
            raise ValueError("a plane dump: the 3D view needs a volume (--dump E@volume@GHz)")
        span = [x[-1] - x[0] for x in xs]
        h = max(span) / n
        hz = h / zfine
        self.axes = [np.linspace(x[0], x[-1], max(2, int(round(s / (hz if k == 2 else h))) + 1))
                     for k, (x, s) in enumerate(zip(xs, span))]
        d = np.asarray(data)
        for k in range(3):
            d = _resample_axis(d, xs[k], self.axes[k], k)
        self.data = d                                                   # (nx, ny, nz, 3)
        self.origin = np.array([a[0] for a in self.axes])
        self.step = np.array([a[1] - a[0] for a in self.axes])
        self.complex = np.iscomplexobj(d)

    def scalar(self, component='mag', phase=None):
        """The view's values: |F| (frequency domain without a phase: the envelope), |Re(F e^jphi)| with a phase,
        or one component (signed: Re(F_k e^jphi))."""
        d = self.data
        if self.complex and phase is None:                            # the envelope over a period
            return np.sqrt((np.abs(d) ** 2).sum(-1)) if component == 'mag' else np.abs(d[..., 'xyz'.index(component)])
        if self.complex:
            d = np.real(d * np.exp(1j * phase))
        return np.sqrt((d ** 2).sum(-1)) if component == 'mag' else d[..., 'xyz'.index(component)]

    def signed(self, component, phase=None):
        """Does scalar() give signed values (a component at an instant)?"""
        return component != 'mag' and (not self.complex or phase is not None)


# ---- colour ---------------------------------------------------------------------------------------
def _cmap(name):
    import matplotlib
    return matplotlib.colormaps[name]


def colour(v, lo, hi, log=True, signed=False, opacity=0.6, gamma=1.5, cmap=None):
    """RGBA voxels (uint8): the colour map of v between lo and hi (log: magnitudes, decades below hi), alpha
    rising with the value (gamma > 1: the weak field fades out, the strong field stays visible through it)."""
    if signed:
        t = np.clip(0.5 + 0.5 * v / (hi or 1.0), 0, 1)
        a = np.abs(2 * t - 1)
        cm = _cmap(cmap or 'RdBu_r')
    else:
        if log:
            lv = np.log10(np.maximum(v, lo))
            t = np.clip((lv - np.log10(lo)) / max(np.log10(hi) - np.log10(lo), 1e-9), 0, 1)
        else:
            t = np.clip((v - lo) / max(hi - lo, 1e-30), 0, 1)
        a = t
        cm = _cmap(cmap or 'inferno')
    rgba = (cm(t.ravel())[:, :4] * 255).astype(np.uint8).reshape(v.shape + (4,))
    rgba[..., 3] = (255 * opacity * a ** gamma).astype(np.uint8)
    return rgba


def levels(vals, signed, log, decades=2.5, clip=99.9):
    """(lo, hi) of a set of frames: symmetric for a signed component, `decades` below the top for log. The top is
    the `clip` percentile of |values| (a port's gap, a few voxels, would otherwise take the whole colour range)."""
    a = np.abs(np.concatenate([np.ravel(v) for v in vals]))
    hi = float(np.nanpercentile(a, clip)) or float(np.nanmax(a)) or 1.0
    if signed:
        return -hi, hi
    return (hi * 10 ** -decades, hi) if log else (0.0, hi)


# ---- the board ------------------------------------------------------------------------------------
def copper_triangles(geometry):
    """The copper of dump_info.json's geometry as triangle meshes: [(layer, z mm, vertices (n, 3), faces
    (m, 3))], model mm (y up, as the dumps)."""
    import shapely
    from shapely.geometry import Polygon
    out = []
    cu_z = geometry.get('cu_z', {})
    for lay, polys in (geometry.get('copper') or {}).items():
        z = cu_z.get(lay)
        if z is None or not polys:
            continue
        verts, faces = [], []
        for rings in polys:
            p = Polygon(rings[0], rings[1:]).buffer(0)
            if p.is_empty:
                continue
            for t in shapely.get_parts(shapely.constrained_delaunay_triangles(p)):
                c = np.asarray(t.exterior.coords)[:3]
                faces.append(np.arange(3) + len(verts) * 3)
                verts.append(np.column_stack([c, np.full(3, z)]))
        if verts:
            out.append((lay, float(z), np.concatenate(verts), np.asarray(faces)))
    return out


def copper_solids(geometry, thickness):
    """The copper as slabs `thickness` mm thick (centred on each layer: shown thicker than it is, so that its
    edges catch the light): [(layer, z mm, vertices (n, 3), faces (m, 3))], faces wound with outward normals
    (top +z, bottom -z, walls away from the copper) for a shaded mesh."""
    import shapely
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    out = []
    cu_z, h = geometry.get('cu_z', {}), 0.5 * thickness
    for lay, polys in (geometry.get('copper') or {}).items():
        z = cu_z.get(lay)
        if z is None or not polys:
            continue
        tri = []                                                    # (3, 3) vertex triples, wound outward
        for rings in polys:
            g = Polygon(rings[0], rings[1:]).buffer(0)
            for p in shapely.get_parts(g):
                if p.is_empty or p.geom_type != 'Polygon':
                    continue
                p = orient(p, 1.0)                                  # exterior counter-clockwise, holes clockwise
                for t in shapely.get_parts(shapely.constrained_delaunay_triangles(p)):
                    c = np.asarray(t.exterior.coords)[:3]
                    if (c[1, 0] - c[0, 0]) * (c[2, 1] - c[0, 1]) - (c[1, 1] - c[0, 1]) * (c[2, 0] - c[0, 0]) < 0:
                        c = c[::-1]                                 # counter-clockwise seen from above
                    tri.append(np.column_stack([c, np.full(3, z + h)]))
                    tri.append(np.column_stack([c[::-1], np.full(3, z - h)]))
                for ring in [p.exterior] + list(p.interiors):
                    r = np.asarray(ring.coords)
                    for a, b in zip(r[:-1], r[1:]):                 # a wall quad as two triangles: normal (dy, -dx)
                        lo_a, lo_b, hi_a, hi_b = (*a, z - h), (*b, z - h), (*a, z + h), (*b, z + h)
                        tri.append(np.array([lo_a, lo_b, hi_b]))
                        tri.append(np.array([lo_a, hi_b, hi_a]))
        if tri:
            v = np.concatenate(tri)
            out.append((lay, float(z), v, np.arange(len(v)).reshape(-1, 3)))
    return out


def copper_outlines(geometry):
    """Every copper ring as line segments at its layer's height: (2 k, 3) points (pairs), model mm."""
    cu_z, segs = geometry.get('cu_z', {}), []
    for lay, polys in (geometry.get('copper') or {}).items():
        z = cu_z.get(lay)
        if z is None:
            continue
        for rings in polys:
            for r in rings:
                a = np.asarray(r, float)
                if len(a) < 2:
                    continue
                p = np.column_stack([a, np.full(len(a), z)])
                segs.append(np.stack([p[:-1], p[1:]], 1).reshape(-1, 3))
    return np.concatenate(segs) if segs else np.zeros((0, 3))


def via_lines(geometry):
    """Vias as vertical segments: (2 k, 3) points (pairs), model mm."""
    v = np.asarray(geometry.get('vias') or [], float).reshape(-1, 5)
    if not len(v):
        return np.zeros((0, 3))
    a = np.column_stack([v[:, 0], v[:, 1], v[:, 3]])
    b = np.column_stack([v[:, 0], v[:, 1], v[:, 4]])
    return np.stack([a, b], 1).reshape(-1, 3)


def board_box(geometry):
    """(xmin, ymin, xmax, ymax, z0, z1) of the board outline and stack (model mm), or None."""
    ol = np.asarray(geometry.get('outline') or [], float)
    if not len(ol):
        return None
    zs = list((geometry.get('cu_z') or {}).values())
    return (*ol.min(0), *ol.max(0), min(zs, default=0.0), max(zs, default=geometry.get('thickness', 1.6)))


def is_volume(src):
    """Is a dump a volume (more than one point along every axis)?"""
    try:
        return all(n > 1 for n in src.shape())
    except Exception:
        return False
