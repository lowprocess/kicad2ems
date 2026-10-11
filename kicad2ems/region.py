"""
Regions and ports beyond port footprints (KiCad coordinates, mm, y down):

- region_polygon(board, region): the area to simulate from a rectangle (x0, y0, x1, y1), a list of
  points, or a drawing layer ('User.1', 'Dwgs.User', ...) whose closed shapes outline it.
- auto_nets(board, points, outline): the nets under the port positions plus the ground nets.
- ground_nets(items, shapes, outline): the nets that look like ground (any coordinates, consistently).
- extra_port_fps(board, specs): ports given in the settings - 'P3=J1.1' (on pad 1 of J1) or
  'P3@x,y[,layer[,rot]]' (at a point) - as footprint-like dicts for Board._port.
- cut_port_fps(board, region, nets, layers): a port wherever a track of the signal nets crosses the
  region edge, pointing into the region (the cut line ends there, terminated in the port impedance).
"""
import math, re
import numpy as np
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union, polygonize

GROUND_RE = re.compile(r'(^|[^A-Z])(A|D|P|SIG|CHASSIS)?(GND|GROUND|VSS)', re.I)


def region_polygon(board, region):
    """Polygon (KiCad coordinates) of a region setting, or None for no region."""
    if region is None or region == '' or region == []:
        return None
    if isinstance(region, str):
        rings = board.drawings.get(region)
        if not rings:
            layers = ", ".join(sorted(board.drawings)) or "none"
            raise ValueError(f"region {region!r}: no shapes on that layer (drawing layers with shapes: {layers})")
        faces = [p for p in polygonize(unary_union(rings)) if p.area > 0]
        if not faces:
            raise ValueError(f"region {region!r}: the shapes on that layer do not close an area")
        return Polygon(max(faces, key=lambda p: p.area).exterior)
    vals = list(region)
    if len(vals) == 4 and all(isinstance(v, (int, float)) for v in vals):
        x0, y0, x1, y1 = vals
        return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    pts = [tuple(map(float, p)) for p in vals]
    if len(pts) < 3:
        raise ValueError("region: a rectangle x0,y0,x1,y1, at least three points, or a drawing layer name")
    poly = Polygon(pts)
    if not poly.is_valid or poly.area <= 0:
        raise ValueError("region: the points do not outline an area")
    return poly


def ground_nets(items, shapes, outline=None):
    """Nets that look like ground: named so (GND, AGND, VSS, ...) among the copper items [(layer, net,
    geometry)], or whose shapes (zones, drawn copper) cover at least a fifth of the outline on some layer.
    A part to such a net is a shunt to ground; with nets = auto they are always modelled."""
    out = {str(n) for _, n, _ in items if n and GROUND_RE.search(str(n))}
    if outline is not None:
        area = {}
        for l, n, g in shapes:
            if n and l.endswith('.Cu'):
                area[(l, n)] = area.get((l, n), 0.0) + g.intersection(outline).area
        out |= {str(n) for (l, n), a in area.items() if a >= 0.2 * outline.area}
    return out


def net_at(board, layer, x, y):
    """Net of the copper on `layer` at KiCad point (x, y), or None."""
    pt = Point(x, y)
    for l, n, g in board.items:
        if l == layer and n and g.distance(pt) < 1e-6:
            return str(n)
    return None


def auto_nets(board, points, outline=None):
    """nets = 'auto': the nets under the ports [(layer, x, y)] plus the ground nets."""
    nets = {net_at(board, l, x, y) for l, x, y in points} - {None}
    return sorted(nets | ground_nets(board.items, getattr(board, 'shapes', []), outline))


def _pad(board, ref, name):
    fp = next((f for f in board.footprints if f['ref'] == ref), None)
    if fp is None:
        raise ValueError(f"port on pad {ref}.{name}: no footprint {ref}")
    pads = [q for q in fp['pads'] if str(q['name']) == str(name)]
    if not pads:
        raise ValueError(f"port on pad {ref}.{name}: {ref} has pads {', '.join(sorted({str(q['name']) for q in fp['pads']}))}")
    return fp, pads[0]


def _track_dir_from(board, layer, geom, xy):
    """Unit direction (model coordinates, y up) from xy along the track that leaves the copper `geom`
    on `layer`, or None."""
    best = None
    for t in board.tracks:
        if t['layer'] != layer:
            continue
        for a, b in ((0, -1), (-1, 0)):
            pa, pb = np.asarray(t['pts'][a], float), np.asarray(t['pts'][b], float)
            if geom.distance(Point(*pa)) < 1e-6 and geom.distance(Point(*pb)) > 1e-6:
                d = pb - pa
                d = np.array([d[0], -d[1]]) / (np.linalg.norm(d) or 1.0)
                if best is None:
                    best = d
    return best


def _rot(d):
    return float(math.degrees(math.atan2(d[1], d[0])) % 360.0)


def parse_port_spec(spec):
    """'P3=J1.1' -> ('P3', 'pad', ('J1', '1')); 'P3@x,y[,layer[,rot]]' -> ('P3', 'at', (x, y, layer, rot))."""
    s = str(spec).replace(' ', '')
    m = re.fullmatch(r'(P\d+[+-]?)=([^.]+)\.(.+)', s, re.I)
    if m:
        return m.group(1).upper(), 'pad', (m.group(2), m.group(3))
    m = re.fullmatch(r'(P\d+[+-]?)@([-\d.eE]+),([-\d.eE]+)(?:,([A-Za-z0-9_.]+))?(?:,([-\d.eE]+))?', s, re.I)
    if m:
        return m.group(1).upper(), 'at', (float(m.group(2)), float(m.group(3)), m.group(4), (float(m.group(5)) if m.group(5) else None))
    raise ValueError(f"port {spec!r}: write P<n>=<ref>.<pad> (on a pad) or P<n>@x,y[,layer[,rotation]] (at a point, KiCad mm)")


def extra_port_fps(board, specs):
    """{port number: footprint-like dict} for port specs (see parse_port_spec)."""
    out = {}
    for spec in specs or []:
        name, kind, arg = parse_port_spec(spec)
        num = int(re.sub(r'\D', '', name))
        if kind == 'pad':
            fp, q = _pad(board, *arg)
            layer = next((l for l in q['layers'] if l.endswith('.Cu')), 'F.Cu')
            d = _track_dir_from(board, layer, q['geom'], q['xy'])
            if d is None:                                      # no track: along the pad's longer side
                b = q['geom'].bounds
                d = np.array([1.0, 0.0]) if (b[2] - b[0]) >= (b[3] - b[1]) else np.array([0.0, 1.0])
            x, y = q['xy']
            props = {'port.layer': layer}
            out[num] = dict(ref=f"{arg[0]}.{arg[1]}", x=float(x), y=float(y), rot=_rot(_axis(d)),
                            side='B' if layer == 'B.Cu' else 'F', props=props, source=f"pad {arg[0]}.{arg[1]}")
        else:
            x, y, layer, rot = arg
            layer = layer or 'F.Cu'
            if rot is None:
                d = _dir_along_track(board, layer, x, y)
                if d is None:
                    raise ValueError(f"port {spec!r}: no track on {layer} at ({x}, {y}); give the rotation")
                rot = _rot(_axis(d))
            out[num] = dict(ref=name, x=x, y=y, rot=rot, side='B' if layer == 'B.Cu' else 'F',
                            props={'port.layer': layer}, source=f"point ({x:g}, {y:g})")
    return out


def _axis(d):
    """The x or y direction nearest to d (lumped ports run along x or y)."""
    return np.array([np.sign(d[0]), 0.0]) if abs(d[0]) >= abs(d[1]) else np.array([0.0, np.sign(d[1])])


def _dir_along_track(board, layer, x, y):
    """Direction (model coordinates) along the track through (x, y), toward its farther end."""
    pt = Point(x, y)
    for t in board.tracks:
        if t['layer'] != layer:
            continue
        line = LineString(t['pts'])
        if line.distance(pt) <= t['width'] / 2 + 1e-6:
            a, b = np.asarray(t['pts'][0], float), np.asarray(t['pts'][-1], float)
            far = a if np.hypot(*(a - (x, y))) > np.hypot(*(b - (x, y))) else b
            d = far - (x, y)
            return np.array([d[0], -d[1]]) / (np.linalg.norm(d) or 1.0)
    return None


def cut_port_fps(board, region, nets, layers, first=1):
    """Ports where tracks of `nets` cross the edge of `region` (KiCad polygon), each just inside the edge
    and pointing into the region. Returns ({port number: footprint-like dict}, notes)."""
    out, notes, n = {}, [], first
    edge = region.exterior
    for t in board.tracks:
        if t['net'] not in nets or t['layer'] not in layers:
            continue
        pts = np.asarray(t['pts'], float)
        for a, b in zip(pts[:-1], pts[1:]):
            seg = LineString([a, b])
            hit = seg.intersection(edge)
            for h in getattr(hit, 'geoms', [hit]):
                if h.is_empty or h.geom_type != 'Point':
                    continue
                q = np.array([h.x, h.y])
                d = b - a
                d = d / (np.linalg.norm(d) or 1.0)
                if not region.contains(Point(*(q + d * 1e-3))):
                    d = -d                                     # point into the region
                dm = np.array([d[0], -d[1]])                   # model coordinates
                if min(abs(dm[0]), abs(dm[1])) > 1e-3:
                    notes.append(f"{t['net']} crosses the region edge at ({q[0]:.3f}, {q[1]:.3f}) at an angle: "
                                 f"no cut port (lumped ports run along x or y)")
                    continue
                c = q + d * 1e-3
                out[n] = dict(ref=f"cut{n}", x=float(c[0]), y=float(c[1]), rot=_rot(dm),
                              side='B' if t['layer'] == 'B.Cu' else 'F', props={'port.layer': t['layer']},
                              source=f"cut ({q[0]:.3f}, {q[1]:.3f})")
                n += 1
    return out, notes
