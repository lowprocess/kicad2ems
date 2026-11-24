"""KiCad .kicad_pcb (version 6 to 10) -> Board.

Read: the stack-up, copper of every net (zone fills, tracks, arcs, pads, via
pads, copper graphics), vias and plated pins (both join layers), and the
Edge.Cuts outline. Zones saved without fills can be filled approximately
(zone outline minus other nets' copper grown by the zone clearance) - KiCad
fills them exactly, so refill and save in KiCad when it matters.
"""
import math
import re
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from ..model import Board, Layer, Via
from . import shapes as shp
from .sexpr import find, findall, parse, val, xy


def _cu_order(name):
    """Sort key of a copper layer name: F.Cu, In1.Cu .. InN.Cu, B.Cu."""
    if name == 'F.Cu':
        return 0
    if name == 'B.Cu':
        return 10_000
    m = re.match(r'In(\d+)\.Cu$', name)
    return int(m.group(1)) if m else 5_000


def _pts(node):
    """The outline points of a (pts (xy ..) (arc ..) ..) child."""
    out = []
    for c in find(node, 'pts') or []:
        if isinstance(c, list) and c[0] == 'xy':
            out.append((float(c[1]), float(c[2])))
        elif isinstance(c, list) and c[0] == 'arc':
            out.extend(map(tuple, shp.arc3(xy(c, 'start'), xy(c, 'mid'), xy(c, 'end'))))
    return out


def _rot(px, py, deg):
    """A footprint-local offset rotated by a KiCad angle (y down)."""
    a = math.radians(deg)
    return px * math.cos(a) + py * math.sin(a), -px * math.sin(a) + py * math.cos(a)


class _Reader:
    def __init__(self, root, fill_unfilled):
        self.root, self.fill_unfilled = root, fill_unfilled
        self.notes = []
        # net code -> name (KiCad <= 9 lists them; 10 writes names in place)
        self.netnames = {n[1]: n[2] for n in findall(root, 'net') if len(n) > 2}
        names = [str(l[1]) for l in (find(root, 'layers') or [])[1:]
                 if isinstance(l, list) and len(l) > 1]
        self.cu = sorted((n for n in names if n.endswith('.Cu')), key=_cu_order)
        self.parts = defaultdict(list)           # (layer, net) -> [geometry]
        self.vias, self.edges, self.unfilled = [], [], []

    # ---- helpers -----------------------------------------------------------
    def net(self, node):
        n = find(node, 'net')
        if n is None or len(n) < 2:
            return ''
        if len(n) > 2:                           # (net 3 "GND")
            return n[2]
        return self.netnames.get(n[1], n[1]) if self.netnames else n[1]

    def layers(self, node):
        """Copper layers named by (layer ..) / (layers ..), wildcards expanded."""
        out = []
        for key in ('layer', 'layers'):
            c = find(node, key)
            for name in (c[1:] if c else []):
                if name in ('*.Cu', 'F&B.Cu'):
                    out += self.cu if name == '*.Cu' else ['F.Cu', 'B.Cu']
                elif name in self.cu:
                    out.append(name)
        return list(dict.fromkeys(out))

    # ---- stack-up ----------------------------------------------------------
    def stackup(self):
        setup = find(self.root, 'setup') or []
        st = find(setup, 'stackup')
        out = []
        for l in findall(st or [], 'layer'):
            kind = val(l, 'type', '')
            t = val(l, 'thickness', 0.0, float)
            if l[1] in self.cu:
                out.append(Layer(l[1], 'copper', t or 0.035))
            elif kind in ('core', 'prepreg') or l[1].startswith('dielectric'):
                out.append(Layer(l[1], 'dielectric', t or 0.1,
                                 val(l, 'epsilon_r', 4.5, float),
                                 val(l, 'loss_tangent', 0.02, float)))
        if [l.name for l in out if l.kind == 'copper'] == self.cu:
            return out
        # no stack-up saved: the board thickness shared evenly between layers
        total = val(find(self.root, 'general') or [], 'thickness', 1.6, float)
        d = max(total - 0.035 * len(self.cu), 0.1) / max(len(self.cu) - 1, 1)
        self.notes.append(f'no stack-up in the file: {d:.3f} mm of FR-4 (er 4.5) '
                          'assumed between copper layers')
        out = []
        for i, name in enumerate(self.cu):
            if i:
                out.append(Layer(f'dielectric {i}', 'dielectric', d, 4.5, 0.02))
            out.append(Layer(name, 'copper', 0.035))
        return out

    # ---- copper ------------------------------------------------------------
    def tracks(self):
        lines = defaultdict(list)                 # (layer, net) -> [(line, width)]
        for s in findall(self.root, 'segment'):
            for l in self.layers(s):
                lines[l, self.net(s)].append((LineString([xy(s, 'start'), xy(s, 'end')]),
                                              val(s, 'width', 0.2, float)))
        for a in findall(self.root, 'arc'):
            for l in self.layers(a):
                pts = shp.arc3(xy(a, 'start'), xy(a, 'mid'), xy(a, 'end'))
                lines[l, self.net(a)].append((LineString(pts), val(a, 'width', 0.2, float)))
        for key, items in lines.items():          # vectorised buffer per layer and net
            g = shapely.buffer([i[0] for i in items], [i[1] / 2 for i in items], quad_segs=6)
            self.parts[key].extend(g)

    def graphics(self):
        """Filled copper graphics (gr_poly, gr_rect, gr_circle) on copper layers."""
        for kind in ('gr_poly', 'gr_rect', 'gr_circle'):
            for g in findall(self.root, kind):
                cu = self.layers(g)
                if not cu:
                    continue
                if kind == 'gr_poly':
                    geom = shp.ring_polygon(_pts(g))
                elif kind == 'gr_rect':
                    (x0, y0), (x1, y1) = xy(g, 'start'), xy(g, 'end')
                    geom = shapely.box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                else:
                    c, e = xy(g, 'center'), xy(g, 'end')
                    geom = Point(c).buffer(math.dist(c, e), quad_segs=16)
                for l in cu:
                    self.parts[l, self.net(g)].append(shp.clean(geom))

    def zones(self):
        for z in findall(self.root, 'zone'):
            if find(z, 'keepout') is not None:
                continue                          # a rule area, not copper
            net = self.net(z) or val(z, 'net_name', '')
            fills = defaultdict(list)
            for f in findall(z, 'filled_polygon'):
                fills[val(f, 'layer')].append(shp.ring_polygon(_pts(f)))
            for l in self.layers(z):
                if fills.get(l):
                    self.parts[l, net].extend(fills[l])
                elif find(z, 'polygon') is not None:
                    cp = find(z, 'connect_pads') or []
                    clr = val(cp, 'clearance', 0.2, float)
                    self.unfilled.append((l, net, shp.clean(Polygon(_pts(find(z, 'polygon')))),
                                          clr, val(z, 'min_thickness', 0.2, float)))

    def footprints(self):
        for fp in findall(self.root, 'footprint') + findall(self.root, 'module'):
            at = find(fp, 'at')
            fx, fy = float(at[1]), float(at[2])
            frot = float(at[3]) if len(at) > 3 else 0.0
            for pad in findall(fp, 'pad'):
                ptype, pshape = pad[2], pad[3]
                pat = find(pad, 'at')
                ox, oy = _rot(float(pat[1]), float(pat[2]), frot)
                prot = float(pat[3]) if len(pat) > 3 else 0.0
                if fp[0] == 'module':             # KiCad 5: pad angle relative
                    prot += frot
                sz = find(pad, 'size')
                w, h = float(sz[1]), float(sz[2]) if len(sz) > 2 else float(sz[1])
                local = shp.pad_shape(pshape, w, h, val(pad, 'roundrect_rratio', 0.25, float))
                prims = find(pad, 'primitives')
                if pshape == 'custom' and prims:
                    polys = [Polygon(_pts(p)) for p in findall(prims, 'gr_poly')]
                    local = shp.clean(shapely.union_all([local] + polys))
                geom = shp.place(local, fx + ox, fy + oy, prot)
                net, cu = self.net(pad), self.layers(pad)
                if ptype != 'np_thru_hole':
                    for l in cu:
                        self.parts[l, net].append(geom)
                dr = find(pad, 'drill')
                if ptype == 'thru_hole' and dr and len(cu) >= 2:   # a plated pin joins layers
                    nums = [float(x) for x in dr[1:] if re.fullmatch(r'[\d.]+', str(x))]
                    self.vias.append(Via(fx + ox, fy + oy, min(nums or [0.3]), min(w, h),
                                         cu[0], cu[-1], net))

    def via_items(self):
        for v in findall(self.root, 'via'):
            cu = self.layers(v)
            if not cu:
                continue
            x, y = xy(v)
            size, net = val(v, 'size', 0.6, float), self.net(v)
            i, j = self.cu.index(cu[0]), self.cu.index(cu[-1])
            span = self.cu[min(i, j):max(i, j) + 1]
            self.vias.append(Via(x, y, val(v, 'drill', 0.3, float), size, span[0], span[-1], net))
            disc = Point(x, y).buffer(size / 2, quad_segs=4)
            for l in span:
                self.parts[l, net].append(disc)

    def outline(self):
        segs = []
        for g in self.root:
            if not (isinstance(g, list) and g and str(g[0]).startswith('gr_')):
                continue
            if val(g, 'layer') != 'Edge.Cuts':
                continue
            kind = g[0]
            if kind == 'gr_line':
                segs.append([xy(g, 'start'), xy(g, 'end')])
            elif kind == 'gr_arc' and find(g, 'mid') is not None:
                segs.append(shp.arc3(xy(g, 'start'), xy(g, 'mid'), xy(g, 'end')))
            elif kind == 'gr_rect':
                (x0, y0), (x1, y1) = xy(g, 'start'), xy(g, 'end')
                segs.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
            elif kind == 'gr_circle':
                c, e = xy(g, 'center'), xy(g, 'end')
                segs.append(Point(c).buffer(math.dist(c, e), quad_segs=16).exterior.coords)
            elif kind == 'gr_poly':
                p = _pts(g)
                segs.append(p + p[:1])
        return shp.polygonize_outline(segs)

    # ---- zones saved unfilled ----------------------------------------------
    def fill_zones(self, copper, outline):
        if not self.unfilled:
            return
        if not self.fill_unfilled:
            self.notes.append(f'{len(self.unfilled)} zone(s) saved without fills were left out')
            return
        for l, net, poly, clr, minw in self.unfilled:
            area = poly if outline is None else poly.intersection(outline)
            others = [g for n, g in copper.get(l, {}).items() if n != net]
            holes = [Point(v.x, v.y).buffer(v.pad / 2, quad_segs=4) for v in self.vias
                     if v.net != net and l in self._span(v)]
            block = shapely.union_all(others + holes).buffer(clr, quad_segs=4)
            fill = area.difference(block)
            fill = fill.buffer(-minw / 2).buffer(minw / 2)    # drop necks under min width
            if not fill.is_empty:
                copper.setdefault(l, {})[net] = shapely.union_all([copper[l].get(net, Polygon()), fill])
        self.notes.append(f'{len(self.unfilled)} zone/layer fill(s) were missing in the file and '
                          'were filled approximately (refill in KiCad for exact copper)')

    def _span(self, v):
        i, j = self.cu.index(v.top), self.cu.index(v.bottom)
        return self.cu[min(i, j):max(i, j) + 1]

    def read(self, source):
        stack = self.stackup()
        self.tracks(); self.graphics(); self.zones(); self.footprints(); self.via_items()
        copper = defaultdict(dict)
        for (l, net), geoms in self.parts.items():
            copper[l][net] = shapely.union_all([shp.clean(g) for g in geoms])
        outline = self.outline()
        self.fill_zones(copper, outline)
        return Board(source, stack, dict(copper), self.vias, outline, self.notes)


def read_kicad(path, fill_unfilled=True):
    """Read a .kicad_pcb file into a Board."""
    with open(path, encoding='utf-8') as fh:
        root = parse(fh.read())
    return _Reader(root, fill_unfilled).read(str(path))
