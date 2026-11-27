"""
Drawn shapes: arbitrary 2D and 3D geometry added to a board (or drawn on its
own), from the window's Draw page or by hand. No Qt here: the drawing as data,
its geometry, what it does to a Board, and the triangles a 3D view draws.

    d = Drawing([Shape('rect', [[0, 0], [5, 2]], layer='F.Cu', net='RF'),
                 Shape('circle', [[2, 1]], radius=0.4, material='cut',
                       layer='F.Cu'),
                 Shape('polygon', [[0, 0], [4, 0], [2, 3]], material='metal',
                       z0=1.6, z1=3.0)])
    d.save('drawing.json');  d = Drawing.load('drawing.json')
    notes = apply(board, d)          # its copper into board.copper, its cuts
                                     # out of it; its solids as board.solids
    v, f = prism(polygon, z0, z1)    # a closed triangle mesh, outward normals
    i, t = pick(meshes, origin, direction)
                                     # the mesh a ray meets first (a 3D view's
                                     # click), and how far along it

Coordinates are the model's (mm, y up: board.json's, the 3D view's); a setting
in KiCad's coordinates (a region, a port at a point) has y negated
(region_setting, port_settings).

The kinds of shape (`kind`) and what their points are:

    rect     two opposite corners
    circle   the centre (radius: `radius`)
    polygon  three or more corners
    path     two or more points of a centre line `width` wide (round ends:
             a trace)
    port     one point: a port there (the `ports` setting, P<n>@x,y,layer)

and what they are made of (`material`):

    copper      on a copper layer (`layer`): merged with that layer's copper
                of the same net (`net`; empty: the net of the copper it
                overlaps most, else a net of its own)
    cut         on a copper layer: taken out of every net's copper there
    metal       a solid from z0 to z1 (mm): a shield, a block, a post
    dielectric  a solid of permittivity er and loss tangent tand
    air         a solid of air (a cavity, a slot through the substrate)
    ferrite     a microwave ferrite (er, tand; saturation magnetisation `ms`,
                4 pi Ms in gauss; FMR linewidth `dh`, oersted at 9.4 GHz, the
                datasheets'): magnetised by the drawn magnets, gyrotropic
                (core.magnets has the bias, gfdtd the ferrite)
    magnet      a permanent magnet of remanence `br` (tesla; + magnetised up,
                along +z, - down): a rigid magnetisation for the bias, and to
                the RF a metal (rare-earth magnets conduct; a ceramic magnet's
                RF is a dielectric drawn after it)

Every solver reads board.copper, so drawn copper and cuts reach them all; the
line model builds its sections from the board's own tracks and does not see
them. The solids are kept on the board (board.solids, board.json's 'solids')
and drawn in the 3D view. The FDTD (openems, gfdtd) and Palace mesh them: a
later solid over an earlier one, a solid over the board's dielectric unless it
encloses the whole board (core.solids has the rules). mom25d's layers are
laterally infinite: it refuses a board with solids. The line model and pcbqs
leave them out (apply's notes say so). A ferrite needs gfdtd: openEMS and
Palace refuse it (neither has a gyrotropic medium).
"""
import json, math
from dataclasses import dataclass, field, asdict
import numpy as np

__all__ = ['Shape', 'Drawing', 'apply', 'prism', 'shape_mesh', 'stack_of',
           'ray_hit', 'pick',
           'region_setting', 'port_settings', 'KINDS', 'MATERIALS']

FORMAT, VERSION = 'kicad2ems-shapes', 1
KINDS = ('rect', 'circle', 'polygon', 'path', 'port')
# on a copper layer / solids between two heights
SHEET, SOLID = ('copper', 'cut'), ('metal', 'dielectric', 'air', 'ferrite',
                                   'magnet')
MATERIALS = SHEET + SOLID
# the least number of points of each kind
LEAST = dict(rect=2, circle=1, polygon=3, path=2, port=1)
# a stack to draw on when there is no board: two layers of 1.6 mm FR-4
DEFAULT_STACK = dict(cu_z={'B.Cu': 0.0, 'F.Cu': 1.6},
                     cu_t={'B.Cu': 0.035, 'F.Cu': 0.035}, layers=[
                         dict(name='FR4', z0=0.0, z1=1.6, er=4.3, tand=0.02,
                              kind='diel')
                     ])


@dataclass
class Shape:
    """One drawn shape (the module docstring has the kinds and materials)."""
    kind: str
    points: list
    material: str = 'copper'
    layer: str = 'F.Cu'          # copper, cut and port: the copper layer
    net: str = ''                # copper: the net it joins ('' : found)
    width: float = 0.5           # path: the trace width, mm
    radius: float = 1.0          # circle: mm
    z0: float = 0.0              # solids: from, to (mm)
    z1: float = 1.0
    er: float = 4.3              # dielectric, ferrite
    tand: float = 0.0
    ms: float = 1800.0           # ferrite: 4 pi Ms, gauss
    dh: float = 30.0             # ferrite: FMR linewidth, Oe (at 9.4 GHz)
    br: float = 1.2              # magnet: remanence, T (+ along +z)
    rot: float = None            # port: its direction (KiCad degrees; None:
                                 # along the track under it)
    name: str = ''
    # the Draw page's: not drawn or picked there (still applied), not moved
    hidden: bool = False
    locked: bool = False

    def __post_init__(self):
        self.points = [[float(x), float(y)] for x, y in self.points]
        if self.kind not in KINDS:
            raise ValueError(f"shape kind {self.kind!r}: one of "
                             f"{', '.join(KINDS)}")
        if self.material not in MATERIALS:
            raise ValueError(f"shape material {self.material!r}: one of "
                             f"{', '.join(MATERIALS)}")
        if len(self.points) < LEAST[self.kind]:
            raise ValueError(f"a {self.kind} needs at least "
                             f"{LEAST[self.kind]} point(s)")

    # ---- what it is ----
    @property
    def solid(self):
        """Whether it is a solid between z0 and z1 (else a sheet on a copper
        layer)."""
        return self.material in SOLID and self.kind != 'port'

    def outline(self, segments=64):
        """Its area as a shapely Polygon (model mm), or None (a port; a shape
        with no area)."""
        from shapely.geometry import Polygon, Point, LineString, box
        p = self.points
        if self.kind == 'rect':
            (x0, y0), (x1, y1) = p[0], p[1]
            g = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        elif self.kind == 'circle':
            g = Point(p[0]).buffer(abs(self.radius),
                                   quad_segs=max(4, segments // 4))
        elif self.kind == 'polygon':
            g = Polygon(p).buffer(0)
        elif self.kind == 'path':
            g = LineString(p).buffer(abs(self.width) / 2,
                                     quad_segs=max(4, segments // 8))
        else:
            return None
        if g.is_empty or g.area <= 1e-12:
            return None
        if g.geom_type == 'MultiPolygon':  # a polygon crossing itself
            g = max(g.geoms, key=lambda q: q.area)
        return g

    def bounds(self):
        """(x0, y0, x1, y1) mm."""
        g = self.outline()
        if g is not None:
            return g.bounds
        a = np.asarray(self.points)
        return (*a.min(0), *a.max(0))

    def label(self):
        what = (self.layer if not self.solid else
                f"z {self.z0:g}..{self.z1:g} mm")
        net = f" {self.net}" if self.net and self.material == 'copper' else ''
        return (f"{self.name or self.kind}: {self.material}{net}, {what}"
                if self.kind != 'port' else
                f"{self.name or 'port'}: port on {self.layer}")

    # ---- editing ----
    def copy(self):
        return Shape(**asdict(self))

    def moved(self, dx, dy):
        """A copy moved by (dx, dy) mm."""
        s = Shape(**asdict(self))
        s.points = [[x + dx, y + dy] for x, y in self.points]
        return s

    def rotated(self, angle, about=None):
        """A copy turned `angle` degrees anticlockwise about the point `about`
        (None: its own centre). A rectangle turned off a right angle becomes
        a polygon; a port's direction turns with it."""
        if about is None:
            x0, y0, x1, y1 = self.bounds()
            about = ((x0 + x1) / 2, (y0 + y1) / 2)
        cx, cy = map(float, about)
        a = math.radians(angle)
        c, s_ = math.cos(a), math.sin(a)
        quarter = abs(angle / 90 - round(angle / 90)) < 1e-9
        if quarter:  # exact: no rounding noise on a right angle
            c, s_ = round(c), round(s_)
        turn = lambda x, y: [round(cx + c * (x - cx) - s_ * (y - cy), 9),
                             round(cy + s_ * (x - cx) + c * (y - cy), 9)]
        s = Shape(**asdict(self))
        if self.kind == 'rect' and not quarter:
            (x0, y0), (x1, y1) = self.points
            s.kind = 'polygon'
            s.points = [turn(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1),
                                                (x0, y1))]
        else:
            s.points = [turn(x, y) for x, y in self.points]
        if self.kind == 'port' and self.rot is not None:
            s.rot = round((self.rot + angle) % 360, 9)
        return s

    def mirrored(self, axis, at=None):
        """A copy mirrored: axis 'x' reverses x (left-right, about the line x
        = at), 'y' reverses y (about y = at); at None: its own centre. A
        port's direction is mirrored too."""
        if axis not in ('x', 'y'):
            raise ValueError(f"mirror axis {axis!r}: 'x' or 'y'")
        k = 0 if axis == 'x' else 1
        if at is None:
            b = self.bounds()
            at = (b[k] + b[k + 2]) / 2
        s = Shape(**asdict(self))
        s.points = [[2 * at - p[0], p[1]] if k == 0 else [p[0], 2 * at - p[1]]
                    for p in self.points]
        if self.kind == 'port' and self.rot is not None:
            s.rot = ((180 - self.rot) if k == 0 else -self.rot) % 360
        return s

    def snap_points(self):
        """[(x, y, what)]: the points a click snaps to on it, what one of
        'corner', 'midpoint', 'centre', 'end' (a trace's), 'rim' (a circle's
        quarter points), 'port'."""
        p = self.points
        if self.kind == 'port':
            return [(p[0][0], p[0][1], 'port')]
        if self.kind == 'circle':
            (cx, cy), r = p[0], abs(self.radius)
            return [(cx, cy, 'centre')] + [
                (cx + dx * r, cy + dy * r, 'rim')
                for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))]
        if self.kind == 'rect':
            (x0, y0), (x1, y1) = p
            ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            closed = True
        else:
            ring, closed = [tuple(q) for q in p], self.kind == 'polygon'
        out = [(x, y, 'corner' if closed else 'end' if k in (0, len(ring) - 1)
                else 'corner') for k, (x, y) in enumerate(ring)]
        legs = list(zip(ring, ring[1:] + (ring[:1] if closed else [])))
        out += [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 'midpoint')
                for a, b in legs]
        if self.kind == 'rect':
            out.append(((x0 + x1) / 2, (y0 + y1) / 2, 'centre'))
        return out

    def handles(self):
        """The points to drag to reshape it: its own points, and a circle's
        rim (radius) east of the centre."""
        h = [tuple(p) for p in self.points]
        if self.kind == 'circle':
            h.append((self.points[0][0] + self.radius, self.points[0][1]))
        return h

    def drag_handle(self, i, x, y):
        """Handle i moved to (x, y)."""
        if self.kind == 'circle' and i == len(self.points):
            cx, cy = self.points[0]
            self.radius = max(1e-3, math.hypot(x - cx, y - cy))
        else:
            self.points[i] = [float(x), float(y)]

    def lifted(self, dz, face=None):
        """A copy of a solid raised by dz mm: both faces, or only its 'top'
        (z1) or 'bottom' (z0) face (`face`), never past the other one."""
        s = Shape(**asdict(self))
        lo, hi = sorted((s.z0, s.z1))
        if face == 'top':
            hi = max(hi + dz, lo + 1e-3)
        elif face == 'bottom':
            lo = min(lo + dz, hi - 1e-3)
        else:
            lo, hi = lo + dz, hi + dz
        s.z0, s.z1 = round(lo, 6), round(hi, 6)
        return s

    def to_dict(self):
        d = asdict(self)
        # only what this kind and material use
        if self.kind != 'path':
            d.pop('width')
        if self.kind != 'circle':
            d.pop('radius')
        if not self.solid:
            for k in ('z0', 'z1'):
                d.pop(k)
        if self.material not in ('dielectric', 'ferrite'):
            for k in ('er', 'tand'):
                d.pop(k)
        if self.material != 'ferrite':
            for k in ('ms', 'dh'):
                d.pop(k)
        if self.material != 'magnet':
            d.pop('br')
        if self.kind != 'port':
            d.pop('rot')
        if self.material != 'copper':
            d.pop('net')
        if self.solid:
            d.pop('layer')
        for k in ('hidden', 'locked'):  # (written only when set)
            if not d[k]:
                d.pop(k)
        return d

    @classmethod
    def from_dict(cls, d):
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Drawing:
    """Shapes in the order they are applied (a cut takes out what was drawn
    before it on its layer, not after)."""
    shapes: list = field(default_factory=list)

    def to_dict(self):
        return dict(format=FORMAT, version=VERSION, units='mm',
                    coordinates='model (y up)',
                    shapes=[s.to_dict() for s in self.shapes])

    @classmethod
    def from_dict(cls, d):
        if d.get('format') != FORMAT:
            raise ValueError(
                f"not a {FORMAT} file (format {d.get('format')!r})")
        if int(d.get('version', 0)) > VERSION:
            raise ValueError(
                f"drawing version {d['version']} is newer than this program "
                f"reads ({VERSION}): update kicad2ems")
        return cls([Shape.from_dict(s) for s in d.get('shapes', [])])

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(self.to_dict(), fh, indent=1)
        return path

    @classmethod
    def load(cls, path):
        with open(path, encoding='utf-8') as fh:
            return cls.from_dict(json.load(fh))

    def sheets(self):
        return [s for s in self.shapes if not s.solid and s.kind != 'port']

    def solids(self):
        return [s for s in self.shapes if s.solid]

    def ports(self):
        return [s for s in self.shapes if s.kind == 'port']

    def bounds(self):
        b = [s.bounds() for s in self.shapes]
        if not b:
            return None
        b = np.asarray(b)
        return (*b[:, :2].min(0), *b[:, 2:].max(0))


# ---- onto a board ----------------------------------------------------------
def apply(board, drawing, log=None):
    """The drawing's copper merged into board.copper (and copper_nets), its
    cuts taken out, each clipped to the board outline, in the drawing's order;
    its solids as board.solids (dicts: shape, material, z0, z1, er, tand,
    polygon as GeoJSON; a ferrite's ms and dh, a magnet's br). Returns notes (what was done, and which solvers do
    not see what). ValueError: a shape on a layer the board has no copper
    layer of."""
    from shapely.geometry import mapping
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    from .geometry import polygons
    notes = []
    clip = board.outline
    added = cut = 0
    for k, s in enumerate(drawing.sheets()):
        if s.layer not in board.cu_z:
            raise ValueError(
                f"drawn shape {s.name or s.kind}: no copper layer "
                f"{s.layer!r} (the board's: {', '.join(board.cu_order)})")
        g = s.outline()
        if g is None:
            continue
        g = g.intersection(clip)
        if g.is_empty:
            notes.append(f"drawn {s.name or s.kind} is off the board: left out")
            continue
        ps = list(board.copper.get(s.layer, []))
        ns = list(board.copper_nets.get(s.layer, [''] * len(ps)))
        if s.material == 'cut':
            out_p, out_n = [], []
            for p, n in zip(ps, ns):
                for q in polygons(p.difference(g)):
                    if q.area > 1e-6:
                        out_p.append(orient(q, 1.0))
                        out_n.append(n)
            cut += 1
        else:
            net = s.net or _net_under(ps, ns, g) or f"DRAWN_{s.name or k + 1}"
            join = [i for i, (p, n) in enumerate(zip(ps, ns))
                    if n == net and p.intersects(g)]
            shorts = sorted({n for p, n in zip(ps, ns)
                             if n != net and n and p.intersects(g)
                             and p.intersection(g).area > 1e-6})
            if shorts:
                notes.append(
                    f"drawn {s.name or s.kind} ({net}) overlaps the copper of "
                    f"{', '.join(shorts)} on {s.layer}: a short")
            merged = unary_union([g] + [ps[i] for i in join])
            out_p = [p for i, p in enumerate(ps) if i not in join]
            out_n = [n for i, n in enumerate(ns) if i not in join]
            for q in polygons(merged):
                out_p.append(orient(q, 1.0))
                out_n.append(net)
            if board.sel is not None:
                board.sel.add(net)
            added += 1
        board.copper[s.layer], board.copper_nets[s.layer] = out_p, out_n
    if added or cut:
        notes.append(
            f"drawn shapes: {added} added to the copper, {cut} cut out of it "
            "(the line model does not see them)")
    board.solids = []
    for s in drawing.solids():
        g = s.outline()
        if g is None:
            continue
        diel = s.material in ('dielectric', 'ferrite')
        d = dict(shape=s.name or s.kind, material=s.material,
                 z0=float(min(s.z0, s.z1)), z1=float(max(s.z0, s.z1)),
                 er=float(s.er if diel else 1.0),
                 tand=float(s.tand if diel else 0.0), polygon=mapping(g))
        if s.material == 'ferrite':
            d.update(ms=float(s.ms), dh=float(s.dh))
        elif s.material == 'magnet':
            d.update(br=float(s.br))
        board.solids.append(d)
    if board.solids:
        notes.append(
            f"{len(board.solids)} drawn solid(s): the FDTD (openems, gfdtd) "
            "and palace mesh them; mom25d cannot (laterally infinite layers: "
            "it refuses the board), the line model and pcbqs leave them out")
        if any(d['material'] == 'ferrite' for d in board.solids):
            notes.append(
                "a drawn ferrite: gyrotropic on gfdtd only (openems and palace "
                "refuse it), biased by the drawn magnets (core.magnets)")
    if drawing.ports():
        notes.append(
            f"{len(drawing.ports())} drawn port(s): a run takes them from the "
            "ports setting (the Draw page's 'Ports to settings')")
    if log:
        for n in notes:
            log(n)
    return notes


def _net_under(ps, ns, g):
    """The net of the copper g overlaps most, or ''."""
    area = {}
    for p, n in zip(ps, ns):
        if n and p.intersects(g):
            area[n] = area.get(n, 0.0) + p.intersection(g).area
    area = {n: a for n, a in area.items() if a > 1e-9}
    return max(area, key=area.get) if area else ''


# ---- settings in KiCad's coordinates ---------------------------------------
def region_setting(shape):
    """The `region` setting a shape outlines (KiCad mm, y down): x0,y0,x1,y1
    for a rectangle, [[x, y], ...] corners for anything else."""
    if shape.kind == 'rect':
        x0, y0, x1, y1 = shape.bounds()
        return [round(v, 4) for v in (x0, -y1, x1, -y0)]
    g = shape.outline(segments=32)
    if g is None:
        raise ValueError(f"a {shape.kind} outlines no region")
    return [[round(x, 4), round(-y, 4)] for x, y in g.exterior.coords[:-1]]


def port_settings(drawing, first=1, taken=()):
    """The `ports` setting of the drawing's ports: P<n>@x,y,layer[,rot] (KiCad
    mm), numbered from `first`, skipping the numbers in `taken`."""
    out, n = [], first
    for s in drawing.ports():
        while n in taken:
            n += 1
        x, y = s.points[0]
        spec = f"P{n}@{x:.4g},{-y:.4g},{s.layer}"
        if s.rot is not None:
            spec += f",{s.rot:g}"
        out.append(spec)
        n += 1
    return out


# ---- for a 3D view ---------------------------------------------------------
def stack_of(layout):
    """cu_z, cu_t and the dielectric layers of a board's dict (Board.to_dict,
    core.layout.board_layout), or the default two-layer stack for None."""
    if not layout:
        return dict(DEFAULT_STACK)
    st = layout['stack']
    return dict(cu_z={c['name']: c['z'] for c in st['copper']},
                cu_t={c['name']: c['t'] for c in st['copper']},
                layers=[dict(l) for l in st['layers']])


def prism(g, z0, z1):
    """Polygon g (holes allowed) extruded from z0 to z1: (vertices (n, 3),
    faces (m, 3)), the faces wound outward (top +z, bottom -z, walls away from
    the solid)."""
    import shapely
    from shapely.geometry.polygon import orient
    z0, z1 = sorted((float(z0), float(z1)))
    g = orient(g, 1.0)  # exterior counter-clockwise, holes clockwise
    t = shapely.get_parts(shapely.constrained_delaunay_triangles(g))
    t = t[shapely.get_type_id(t) == 3]
    c = shapely.get_coordinates(shapely.get_exterior_ring(t)).reshape(
        -1, 4, 2)[:, :3]
    cw = ((c[:, 1, 0] - c[:, 0, 0]) * (c[:, 2, 1] - c[:, 0, 1])
          - (c[:, 1, 1] - c[:, 0, 1]) * (c[:, 2, 0] - c[:, 0, 0])) < 0
    c[cw] = c[cw, ::-1]
    at = lambda c, z: np.concatenate([c, np.full(c.shape[:2] + (1,), z)], 2)
    tri = [at(c, z1), at(c[:, ::-1], z0)]
    for ring in [g.exterior, *g.interiors]:
        xy = np.asarray(ring.coords, float)
        a, b = xy[:-1], xy[1:]
        lo_a, lo_b = (np.c_[e, np.full(len(e), z0)] for e in (a, b))
        hi_a, hi_b = (np.c_[e, np.full(len(e), z1)] for e in (a, b))
        tri += [np.stack([lo_a, lo_b, hi_b], 1), np.stack([lo_a, hi_b, hi_a], 1)]
    v = np.concatenate(tri).reshape(-1, 3)
    return v, np.arange(len(v)).reshape(-1, 3)


# shown thicker than copper is, so a sheet catches the light
SHEET_SHOWN = 0.05


def shape_mesh(s, stack):
    """The triangles a 3D view draws for a shape (vertices, faces), or None:
    a sheet as a thin slab at its layer's height, a solid from z0 to z1, a port
    as a small marker post. stack: stack_of's dict."""
    if s.kind == 'port':
        from shapely.geometry import Point
        z = stack['cu_z'].get(s.layer, 0.0)
        return prism(Point(s.points[0]).buffer(0.15, quad_segs=4), z, z + 0.6)
    g = s.outline()
    if g is None:
        return None
    if s.solid:
        if abs(s.z1 - s.z0) < 1e-9:
            return None
        return prism(g, s.z0, s.z1)
    z = stack['cu_z'].get(s.layer)
    if z is None:
        return None
    h = max(stack['cu_t'].get(s.layer, 0.035), SHEET_SHOWN) / 2
    # a cut drawn just proud of the copper, to be seen over it
    lift = 0.01 if s.material == 'cut' else 0.0
    return prism(g, z - h - lift, z + h + lift)


def ray_hit(v, f, origin, direction):
    """How far along the ray origin + t direction (t > 0) it first meets the
    triangles (vertices v (n, 3), faces f (m, 3)), or None (Moller-Trumbore,
    every triangle at once)."""
    t = np.asarray(v, float)[np.asarray(f)]
    if not len(t):
        return None
    o, d = np.asarray(origin, float), np.asarray(direction, float)
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    p = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-12
    inv = 1.0 / np.where(ok, det, 1.0)
    s = o - t[:, 0]
    u = np.einsum('ij,ij->i', s, p) * inv
    q = np.cross(s, e1)
    w = (q @ d) * inv
    dist = np.einsum('ij,ij->i', e2, q) * inv
    hit = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (dist > 1e-9)
    return float(dist[hit].min()) if hit.any() else None


def pick(meshes, origin, direction):
    """(key, t): the mesh of {key: (vertices, faces)} the ray meets first, and
    how far along it; (None, None) for none."""
    best = (None, None)
    for k, m in meshes.items():
        if m is None:
            continue
        t = ray_hit(m[0], m[1], origin, direction)
        if t is not None and (best[1] is None or t < best[1]):
            best = (k, t)
    return best
