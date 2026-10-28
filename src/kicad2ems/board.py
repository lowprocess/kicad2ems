"""
The canonical board: what every solver gets, from any importer (units mm, model coordinates: y up, the
negative of KiCad's y).

    board = Board(raw, nets=['RF', 'GND'], pos_file='board-top.pos', ground='B.Cu')   # resolve an import
    board.save('board.json');  board = Board.load('board.json')                       # the file contract
    board.summary()                       # what the parity checks compare (copper area per layer and net, ...)

  layers         dielectric slabs dict(name, z0, z1, er, tand, kind), bottom to top
  cu_z, cu_t     copper layer heights and thicknesses (cu_order: bottom to top); thickness: the stack's
  outline        the dielectric's extent: the board outline, cropped to the region if one is given
  copper         {layer: [polygons]} the selected copper, merged per layer; copper_nets: the net of each
  vias           [(x, y, radius, z0, z1)] plated holes as metal cylinders; via_nets, via_signal (on a port net)
  ports          [Port] from the P<n> footprints, the settings and region cuts; user_ports groups the legs
                 of differential ports
  parts          [parts.Part] lumped R / L / C parts between two pads
  walls          [(axis, coord, t0, t1, z0, z1)] edge walls where the ground planes end (edges.py)
  prims          the primitives behind the copper, for the line model: items, shapes, tracks, footprints, vias
  ground, ground_nets, signal_nets, sel (the nets kept; None: all), region, cut_sides, notes, source, request

Physical intent only: what is on the board and what was asked for. Nothing is snapped to a mesh, split for
a solver or fitted: the solvers do that, and say so. board.json holds all of it (format kicad2ems-board,
version 1); a reader refuses a newer major version and ignores keys it does not know.
"""
import json
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon, LineString, Point, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from .geometry import polygons, flip_y
from .importers.kicad.outline import board_outline
from .importers.kicad.nets import resolve_nets, DiffLegs
from .importers.kicad.pnp import read_pos, pos_port_refs
from .parts import Part, find_parts

FORMAT, VERSION = 'kicad2ems-board', 1
CUT_KEEPOUT = 2.0      # mm: edge walls on a region cut stay this far from the signal copper (docs/model.md)


def port_field(props, key, default=None):
    """Port footprint field: 'port.<key>' (preferred) or 'mom.<key>' (mom25d footprints)."""
    for k in (f'port.{key}', f'mom.{key}'):
        if props.get(k) not in (None, ''):
            return props[k]
    return default


def cross_section(cu, probe, t, reach=20.0):
    """Width (mm) and mid point of the copper polygon `cu` along the line through `probe` in direction
    t (unit vector): the piece of that line inside the copper nearest to the probe."""
    seg = LineString([probe - t * reach, probe + t * reach]).intersection(cu)
    parts = [seg] if seg.geom_type == 'LineString' else [g for g in getattr(seg, 'geoms', []) if g.geom_type == 'LineString']
    part = min(parts, key=lambda q: q.distance(Point(*probe)))
    (x0, y0), (x1, y1) = part.coords[0], part.coords[-1]
    return float(np.hypot(x1 - x0, y1 - y0)), np.array([(x0 + x1) / 2, (y0 + y1) / 2])


class Port:
    """Lumped port. kind 'z': a vertical sheet across the trace from the reference copper to the trace
    (feed lines over a plane); kind 'gap': a sheet in the copper plane across a gap between two
    conductors on the same layer (centre-fed dipoles, gap feeds), from gap[0] to gap[1] along axis.
    c is the port's centre, sgn the direction into the circuit along axis (0: x, 1: y), w its width."""
    FIELDS = ('name', 'layer', 'z_sig', 'z_ref', 'c', 'axis', 'sgn', 'w', 'z0', 'leg', 'pair', 'ref_layer', 'kind',
              'gap', 'net', 'source')

    def __init__(self, name, layer, z_sig, z_ref, c, axis, sgn, w, z0, leg=None, pair=None, ref_layer='',
                 kind='z', gap=None, net=None, source=''):
        self.name, self.layer, self.z_sig, self.z_ref = name, layer, z_sig, z_ref
        self.c, self.axis, self.sgn, self.w, self.z0 = np.asarray(c, float), axis, sgn, w, z0
        self.leg, self.pair, self.ref_layer = leg, pair, ref_layer
        self.kind, self.gap, self.net = kind, None if gap is None else tuple(gap), net
        self.source = source                        # footprint TP1 / pad J1.1 / point (x, y) / cut (x, y)

    @property
    def direction(self):
        return 'z' if self.kind == 'z' else 'xy'[self.axis]

    def planes(self):
        """Coordinates along the axis that must be mesh lines."""
        return [self.c[self.axis]] if self.kind == 'z' else list(self.gap)

    def box(self):
        """Lumped-port sheet: across the trace, from reference copper to the trace ('z'), or across the
        gap in the copper plane ('gap')."""
        a, b = self.axis, 1 - self.axis
        s, e = np.zeros(3), np.zeros(3)
        s[b], e[b] = self.c[b] - self.w / 2, self.c[b] + self.w / 2
        if self.kind == 'z':
            s[a] = e[a] = self.c[a]
            s[2], e[2] = self.z_ref, self.z_sig
        else:
            s[a], e[a] = self.gap
            s[2] = e[2] = self.z_sig
        return s, e

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.FIELDS}
        return dict(d, c=[float(v) for v in self.c], gap=None if self.gap is None else [float(v) for v in self.gap])

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.FIELDS if k in d})


class Prims:
    """The primitives the copper is made of, in model coordinates (what the line model reads): items
    [(layer, net, geometry)], shapes (the zones and drawn copper among the items), tracks [dict(layer, net,
    width, pts)], vias [dict(x, y, size, drill, layers, net[, pth])] and footprints [dict(ref, value, props,
    x, y, rot, side, dnp, exclude_from_sim, pads [dict(name, xy, layers, net, geom, rot)])]."""

    def __init__(self, items=(), shapes=(), tracks=(), vias=(), footprints=()):
        self.items, self.shapes, self.tracks = list(items), list(shapes), list(tracks)
        self.vias, self.footprints = list(vias), list(footprints)

    @classmethod
    def from_raw(cls, raw, keep, layers, outline):
        """The primitives of an imported board (KiCad coordinates) on copper `layers` that reach `outline`
        (model coordinates): a cropped region of a large board keeps only what it can use. keep: its
        items that do (inside_items)."""
        reach = outline.buffer(1e-6)
        k_reach = flip_y(reach)
        shape_ids = {id(it) for it in getattr(raw, 'shapes', [])}
        items, shapes = [], []
        for it in keep:
            items.append((it[0], it[1], flip_y(it[2])))
            if id(it) in shape_ids:
                shapes.append(items[-1])
        tracks = []
        for t in getattr(raw, 'tracks', []):
            pts = np.column_stack([t['pts'][:, 0], -t['pts'][:, 1]])
            if t['layer'] in layers and LineString(pts).intersects(reach):
                tracks.append(dict(t, pts=pts))
        vias = [dict(v, y=-v['y']) for v in raw.vias if reach.contains(Point(v['x'], -v['y']))]
        fps = []
        for fp in getattr(raw, 'footprints', []):
            if any(l in layers for q in fp['pads'] for l in q['layers']) and any(q['geom'].intersects(k_reach) for q in fp['pads']):
                pads = [dict(q, xy=np.array([q['xy'][0], -q['xy'][1]], float), geom=flip_y(q['geom'])) for q in fp['pads']]
                fps.append(dict({k: v for k, v in fp.items() if k not in ('pads', 'fab')}, y=-fp['y'], pads=pads))
        return cls(items, shapes, tracks, vias, fps)

    def to_dict(self):
        shapes = {id(s) for s in self.shapes}
        return dict(items=[dict(layer=it[0], net=it[1], shape=id(it) in shapes, geom=mapping(it[2])) for it in self.items],
                    tracks=[dict(t, pts=np.asarray(t['pts']).tolist()) for t in self.tracks], vias=self.vias,
                    footprints=[dict(fp, pads=[dict(q, xy=[float(v) for v in q['xy']], geom=mapping(q['geom']))
                                               for q in fp['pads']]) for fp in self.footprints])

    @classmethod
    def from_dict(cls, d):
        items, shapes = [], []
        for it in d.get('items', []):
            items.append((it['layer'], it['net'], shape(it['geom'])))
            if it.get('shape'):
                shapes.append(items[-1])
        tracks = [dict(t, pts=np.asarray(t['pts'], float)) for t in d.get('tracks', [])]
        fps = [dict(fp, pads=[dict(q, xy=np.asarray(q['xy'], float), geom=shape(q['geom'])) for q in fp['pads']])
               for fp in d.get('footprints', [])]
        return cls(items, shapes, tracks, d.get('vias', []), fps)


def inside_items(raw, layers, outline):
    """The items of an imported board (KiCad coordinates) on copper `layers` whose bounding box meets the
    outline's (model coordinates)."""
    cand = [it for it in raw.items if it[0] in layers]
    if not cand:
        return []
    hit = shapely.STRtree([g for _, _, g in cand]).query(flip_y(outline).buffer(1e-6))
    return [cand[i] for i in sorted(hit)]


class Board:
    """The board of a run, resolved from an imported board (`raw`: KiCadBoard, GerberBoard) and the
    selection settings: nets, pick-and-place file, solder mask, region, solid ground layer, lumped parts,
    extra ports, ports where tracks cross the region edge and edge walls. need_ports=False: a board without
    ports is fine (the KiCad plugin's impedance map, before any are placed)."""

    def __init__(self, raw, nets=None, pos_file=None, include_mask=False, region=None, ground=None, parts='auto',
                 log=print, ports=None, cut_ports=False, edge_walls=(), edge_keepout=0.5, need_ports=True):
        from .region import region_polygon, auto_nets, extra_port_fps, ground_nets
        self.log, self.notes, self.need_ports = log, [], need_ports
        self.source = dict(importer=getattr(raw, 'importer', 'kicad'), file=getattr(raw, 'path', ''))
        self.request = dict(nets=nets, pos=pos_file, mask=include_mask, region=region, ground=ground, parts=parts,
                            ports=list(ports or []), cut_ports=cut_ports, edge_walls=list(edge_walls or []),
                            edge_keepout=edge_keepout)
        self._stack(raw, include_mask)
        # ---- outline / region (KiCad coordinates until flipped) ----
        full = board_outline(raw)
        rpoly = region_polygon(raw, region)
        if full is None:
            cop = [g for l, n, g in raw.items if l in self.cu_z]
            full = unary_union(cop).envelope.buffer(1.0, join_style=2)
            self.notes.append("no board outline: using the copper bounding box + 1 mm")
        # ---- nets: as given, all copper, or 'auto' (the nets under the ports + the ground nets) ----
        if nets in ('auto', ['auto'], ('auto',)):
            refs = pos_port_refs(read_pos(pos_file)) if pos_file else None
            fps = dict(raw.port_footprints(refs))
            for i, sp in enumerate(ports or []):                # one at a time: a pair's legs share a number
                fps.update({('spec', i): fp for fp in extra_port_fps(raw, [sp]).values()})
            pts = []
            for fp in fps.values():
                for f in (fp.values() if isinstance(fp, DiffLegs) else [fp]):
                    lay = port_field(f['props'], 'layer') or ('B.Cu' if f['side'] == 'B' else 'F.Cu')
                    pts.append((lay, f['x'], f['y']))
            nets = auto_nets(raw, pts, full)
            self.notes.append(f"nets (auto): {', '.join(nets)}")
        self.sel = resolve_nets(nets, {n for _, n, _ in raw.items} | {v['net'] for v in raw.vias}) if nets else None
        ol = full
        self.region = region
        if rpoly is not None:
            ol = ol.intersection(rpoly)
            if ol.is_empty:
                raise ValueError(f"region {region} does not overlap the board")
        # sides where the region cuts the board (named in KiCad coordinates, as --edge-walls)
        self.cut_sides = []
        if rpoly is not None:
            fb, cb = full.bounds, ol.bounds
            for i, side in enumerate(('xmin', 'ymin', 'xmax', 'ymax')):
                if (cb[i] > fb[i] + 1e-3) if i < 2 else (cb[i] < fb[i] - 1e-3):
                    self.cut_sides.append(side)
            if self.cut_sides:
                self.notes.append(f"region cuts the board on {', '.join(self.cut_sides)}")
        self.outline = flip_y(ol)
        if isinstance(self.outline, MultiPolygon):
            self.outline = max(self.outline.geoms, key=lambda p: p.area)
        self.outline = Polygon(self.outline.exterior)                         # dielectric: outer edge only
        keep = inside_items(raw, set(self.cu_z), self.outline)
        self.prims = Prims.from_raw(raw, keep, set(self.cu_z), self.outline)
        self.ground_nets = ground_nets(self.prims.items, self.prims.shapes, self.outline)
        # ---- copper per layer: the selected nets' items, merged ----
        self.copper, self.copper_nets = {}, {}
        for lay in self.cu_order:
            gs = [g for l, n, g in keep if l == lay and (self.sel is None or n in self.sel)]
            if not gs:
                continue
            g = flip_y(unary_union(gs)).intersection(self.outline).simplify(2e-3, preserve_topology=True)
            ps = [orient(p, 1.0) for p in polygons(g) if p.area > 1e-6]
            if ps:
                self.copper[lay] = ps
        if ground:                                              # model this layer as a solid plane
            if ground not in self.cu_z:
                raise ValueError(f"ground layer {ground} is not a copper layer of the stackup "
                                 f"({', '.join(self.cu_order)})")
            self.copper[ground] = [orient(self.outline, 1.0)]       # union of its copper and the outline = outline
            self.notes.append(f"{ground}: solid ground plane over the board outline (ground = {ground})")
        self.ground = ground
        self._nets_of_copper()
        self._vias()
        self._ports(raw, pos_file, ports or [], cut_ports, rpoly)
        # ---- lumped R / L / C parts ----
        self.parts = []
        if parts not in ('auto', 'none', None):
            raise ValueError(f"parts: 'auto' or 'none', not {parts!r}")
        if parts == 'auto':
            self.parts, notes = find_parts(self)
            self.notes += notes
        self._walls(edge_walls, edge_keepout, cut_ports)

    # ---- construction steps ------------------------------------------------------------------------
    def _stack(self, raw, include_mask):
        items = [it for it in raw.stack_items if it['kind'] in ('copper', 'diel') or
                 (it['kind'] == 'mask' and include_mask)]
        if not items:
            raise ValueError("board has no stackup section: define one in Board Setup > Physical Stackup")
        z = 0.0
        self.layers, self.cu_z, self.cu_t = [], {}, {}
        rz = lambda v: round(v, 6)                              # exact, repeatable heights (sheets must sit on z lines)
        for it in items[::-1]:
            if it['kind'] == 'copper':
                self.cu_z[it['name']] = rz(z)
                self.cu_t[it['name']] = it['t'] * 1e3
            else:
                t = it['t'] * 1e3
                self.layers.append(dict(name=it['name'], z0=rz(z), z1=rz(z + t), er=it.get('er', 4.5),
                                        tand=it.get('tand', 0.02), kind=it['kind']))
                z = rz(z + t)
        self.thickness = z

    @property
    def cu_order(self):
        """Copper layers, bottom to top."""
        return sorted(self.cu_z, key=lambda n: self.cu_z[n])

    def _nets_of_copper(self):
        """copper_nets: per layer, the net of each copper polygon (the selected net covering most of it)."""
        for lay, ps in self.copper.items():
            items = [(n, g) for l, n, g in self.prims.items if l == lay and n and (self.sel is None or n in self.sel)]
            tree = shapely.STRtree([g for _, g in items]) if items else None
            nets = []
            for p in ps:
                hit = tree.query(p, predicate='intersects') if tree is not None else []
                if len({items[i][0] for i in hit}) == 1:             # one net here: no areas to weigh
                    nets.append(str(items[hit[0]][0]))
                    continue
                area = {}
                for i in hit:
                    n, g = items[i]
                    area[n] = area.get(n, 0.0) + g.intersection(p).area
                nets.append(str(max(area, key=area.get)) if area else '')
            self.copper_nets[lay] = nets

    def _vias(self):
        self.vias, self.via_nets = [], []
        inside = self.outline.buffer(1e-6)
        for v in self.prims.vias:
            if self.sel is not None and v['net'] not in self.sel:
                continue
            lys = [l for l in v['layers'] if l in self.cu_z]
            if len(lys) < 2 or not inside.contains(Point(v['x'], v['y'])):
                continue
            zs = [self.cu_z[l] for l in lys]
            self.vias.append((v['x'], v['y'], v['drill'] / 2, min(zs), max(zs)))
            self.via_nets.append(v['net'])

    def _ports(self, raw, pos_file, specs, cut_ports, rpoly):
        from .region import cut_port_fps
        refs = pos_port_refs(read_pos(pos_file)) if pos_file else None
        fps = dict(raw.port_footprints(refs))
        for k, f in fps.items():
            for leg in (f.values() if isinstance(f, DiffLegs) else [f]):
                leg.setdefault('source', f"footprint {leg['ref']}")
        for (n, leg), fp in self._extra_ports(raw, specs).items():     # ports given in the settings (pad / point)
            if leg:
                fps.setdefault(n, DiffLegs())[leg] = fp
            else:
                fps[n] = fp
        if cut_ports and rpoly is not None:
            signal = (set(self.sel) - self.ground_nets) if self.sel is not None else \
                {self._net_at(raw, f) for f in self._single_legs(fps)} - {None}
            cut, notes = cut_port_fps(raw, rpoly, signal, set(self.cu_z), first=max(fps, default=0) + 1)
            fps.update(cut)
            self.notes += notes
            if cut:
                self.notes.append(f"{len(cut)} port(s) where tracks cross the region edge: "
                                  f"{', '.join(f'P{n}' for n in cut)}")
        if not fps and self.need_ports:
            raise ValueError("no ports: no port footprints (Fab text P<n>, or P<n> in the pick-and-place file), "
                             "no ports in the settings and no cut ports")
        inside = self.outline.buffer(1e-6)
        self.port_refs = {f['ref'] for fp in fps.values() for f in (fp.values() if isinstance(fp, DiffLegs) else [fp])}
        port_fps = {}
        for n, fp in fps.items():
            legs = fp.values() if isinstance(fp, DiffLegs) else [fp]
            out = [f for f in legs if not inside.contains(Point(f['x'], -f['y']))]
            if out:
                self.notes.append(f"P{n} ({', '.join(f['ref'] for f in out)}) is outside the "
                                  f"{'region' if self.region else 'board outline'}: left out")
                continue
            port_fps[n] = fp
        if not port_fps and self.need_ports:
            raise ValueError("no port footprints inside the " + ("region" if self.region else "board outline"))
        self.ports = []
        for n, fp in port_fps.items():
            if isinstance(fp, DiffLegs):
                self.ports += [self._port(raw, f"P{n}+", fp['+'], leg='+', pair=f"P{n}"),
                               self._port(raw, f"P{n}-", fp['-'], leg='-', pair=f"P{n}")]
            else:
                self.ports.append(self._port(raw, f"P{n}", fp))

    @property
    def user_ports(self):
        """[(name, [ports])]: one entry per port as the user named it, the two legs of a differential port
        together."""
        out = []
        for p in self.ports:
            if p.pair and out and out[-1][0] == p.pair:
                out[-1][1].append(p)
            else:
                out.append((p.pair or p.name, [p]))
        return out

    @property
    def signal_nets(self):
        """The nets of the traces the ports sit on; vias on them are 'signal vias'."""
        return {p.net for p in self.ports if p.net}

    @property
    def via_signal(self):
        sig = self.signal_nets
        return [n in sig for n in self.via_nets]

    def _walls(self, sides, keepout, cut_ports):
        from .edges import edge_walls, describe
        sides = list(sides or [])
        if not sides and cut_ports and self.cut_sides:
            # a cut through a plane pair leaves it open at the port: close it, well clear of the lines
            sides, keepout = list(self.cut_sides), max(keepout, CUT_KEEPOUT)
            self.notes.append(f"edge walls on the region cut ({', '.join(sides)}), {keepout:g} mm clear of the "
                              f"signal copper")
        self.walls = edge_walls(self, sides, keepout=keepout) if sides else []
        if sides:
            self.notes.append(describe(self.walls, self))

    def __getstate__(self):
        """Pickled for parallel runs: the log callback stays behind."""
        return dict(self.__dict__, log=print)

    def _extra_ports(self, raw, specs):
        from .region import extra_port_fps, parse_port_spec
        out = {}
        for sp in specs:                                    # one at a time: a pair's legs (P1+, P1-) share a number
            name = parse_port_spec(sp)[0]
            for n, fp in extra_port_fps(raw, [sp]).items():
                out[(n, '+' if name.endswith('+') else '-' if name.endswith('-') else '')] = fp
        return out

    @staticmethod
    def _single_legs(fps):
        for f in fps.values():
            yield from (f.values() if isinstance(f, DiffLegs) else [f])

    def _net_at(self, raw, f):
        from .region import net_at
        lay = port_field(f['props'], 'layer') or ('B.Cu' if f['side'] == 'B' else 'F.Cu')
        return net_at(raw, lay, f['x'], f['y'])

    # ---- helpers -----------------------------------------------------------------------------------------
    def copper_at(self, lay, pt):
        """The modelled copper polygon on layer `lay` containing point pt (model coordinates), or None."""
        for p in self.copper.get(lay, []):
            if p.distance(pt) < 1e-6:
                return p
        return None

    def net_at(self, lay, x, y):
        """Net of the selected copper on layer `lay` at model point (x, y), or None."""
        pt = Point(x, y)
        for l, n, g in self.prims.items:
            if l == lay and n and g.distance(pt) < 1e-6 and (self.sel is None or n in self.sel):
                return str(n)
        return None

    def _reference_hint(self):
        if self.sel is not None:
            return "include the ground net in nets, or set ground = <layer>"
        return "no copper on any layer there: the board needs a reference plane, or set ground = <layer>"

    def _port(self, raw, name, fp, leg=None, pair=None):
        props = fp['props']
        lay = port_field(props, 'layer') or ('B.Cu' if fp['side'] == 'B' else 'F.Cu')
        if lay not in self.cu_z:
            raise ValueError(f"{name}: port layer {lay} is not a copper layer of the stackup")
        a = np.radians(fp['rot'])
        d = np.array([np.cos(a), np.sin(a)])
        axis = int(np.argmax(np.abs(d)))
        if abs(abs(d[axis]) - 1) > 1e-3:
            raise ValueError(f"{name} ({fp['ref']}): rotation {fp['rot']} deg - lumped ports need a feed along x or y")
        sgn = int(np.sign(d[axis]))
        c = np.array([fp['x'], -fp['y']])
        source = fp.get('source', f"footprint {fp['ref']}")
        cu = self.copper_at(lay, Point(*c))
        if cu is None and str(port_field(props, 'type', '')).lower() != 'z':
            gp = self._gap_port(name, lay, c, d, axis, sgn, props, leg, pair, source)
            if gp is not None:
                return gp
        if cu is None:
            here = sorted({l for l in self.copper if self.copper_at(l, Point(*c)) is not None})
            raise ValueError(f"{name} ({fp['ref']}) at ({fp['x']:.3f}, {fp['y']:.3f}) mm is not on {lay} copper of the "
                             f"selected nets" + (f"; copper there on {here} - check the footprint field port.layer / "
                                                 f"mom.layer" if here else ''))
        # width across the feed at a point just inside the trace; centre the port on the trace. port.width: a port
        # that wide centred on the footprint instead (a probe feed inside a patch, where the copper is no trace)
        pw = port_field(props, 'width')
        if pw not in (None, ''):
            w, pc = float(pw), c.copy()
        else:
            w, mid = cross_section(cu, c + d * 0.05, np.array([-d[1], d[0]]))
            pc = c + d * min(0.25 * w, 0.1)                           # port plane just inside the trace end
            pc[1 - axis] = mid[1 - axis]
        # reference: nearest copper below (top side) / above (bottom side) under the port
        zs = self.cu_z[lay]
        below = fp['side'] != 'B'
        ref = None
        for side in (below, not below):                         # preferred side first, then the other
            cand = [l for l in self.cu_order if (self.cu_z[l] < zs if side else self.cu_z[l] > zs)]
            cand.sort(key=lambda l: abs(self.cu_z[l] - zs))
            ref = next((l for l in cand if self.copper_at(l, Point(*pc)) is not None), None)
            if ref:
                break
        if ref is None:
            raise ValueError(f"{name}: no reference copper {'below' if below else 'above'} the port at "
                             f"({fp['x']:.3f}, {fp['y']:.3f}) mm - {self._reference_hint()}")
        return Port(name, lay, zs, self.cu_z[ref], pc, axis, sgn, w, float(port_field(props, 'z0', 50)), leg, pair, ref,
                    net=self.net_at(lay, *pc), source=source)

    def _gap_port(self, name, lay, c, d, axis, sgn, props, leg, pair, source, reach=5.0):
        """Port in a gap: copper on lay within `reach` mm on both sides of c along d. The sheet spans the
        gap between the two copper edges; its width is the narrower of the two conductors there."""
        ps = self.copper.get(lay, [])
        ends, widths, mids = [], [], []
        for sg in (-1, 1):
            ray = LineString([c, c + sg * d * reach])
            hits = [ray.intersection(p) for p in ps]
            dist = [Point(*c).distance(h) for h in hits if not h.is_empty]
            if not dist:
                return None
            a = min(dist)
            ends.append(c[axis] + sg * a)
            probe = c + sg * d * (a + 0.05)
            cu = next((p for p in ps if p.distance(Point(*probe)) < 1e-6), None)
            if cu is None:
                return None
            w, mid = cross_section(cu, probe, np.array([-d[1], d[0]]))
            widths.append(w)
            mids.append(mid)
        pc = c.copy()
        pc[1 - axis] = 0.5 * (mids[0][1 - axis] + mids[1][1 - axis])
        zs = self.cu_z[lay]
        return Port(name, lay, zs, zs, pc, axis, sgn, min(widths), float(port_field(props, 'z0', 50)), leg, pair, lay,
                    kind='gap', gap=(min(ends), max(ends)), source=source)

    def describe(self):
        """The board as the log shows it: the stack, the copper, the outline and vias, each port and part, the notes."""
        L = ["stack (bottom to top):"]
        for l in self.layers:
            L.append(f"  {l['name']:<14s} z {l['z0']:.3f}..{l['z1']:.3f} mm  er {l['er']:.2f}  tand {l['tand']:.4f}")
        for n in self.cu_order[::-1]:
            L.append(f"  copper {n:<8s} z {self.cu_z[n]:.3f} mm" + ("" if n in self.copper else "  (nothing selected)"))
        b = self.outline.bounds
        ns = sum(self.via_signal)
        L.append(f"outline {b[2] - b[0]:.2f} x {b[3] - b[1]:.2f} mm, vias {len(self.vias)} ({ns} on signal nets "
                 f"{sorted(self.signal_nets)}, {len(self.vias) - ns} other)")
        for p in self.ports:
            if p.kind == 'gap':
                L.append(f"  {p.name}: lumped gap port on {p.layer} along {p.direction}, gap {p.gap[1] - p.gap[0]:.3f} mm, "
                         f"w {p.w:.3f} mm, at ({p.c[0]:.3f}, {-p.c[1]:.3f}) mm (KiCad), Z {p.z0:g} ohm")
                continue
            L.append(f"  {p.name}: lumped, {p.layer} -> {p.ref_layer}, w {p.w:.3f} mm, h {abs(p.z_sig - p.z_ref):.3f} mm, "
                     f"at ({p.c[0]:.3f}, {-p.c[1]:.3f}) mm (KiCad), Z {p.z0:g} ohm" +
                     (f" [{p.source}]" if p.source and not p.source.startswith('footprint') else ''))
        L += [p.describe() for p in self.parts]
        return "\n".join(L + self.notes)

    # ---- board.json ----------------------------------------------------------------------------------------
    def to_dict(self):
        """The board as plain data (board.json): lengths in mm, model coordinates, polygons as GeoJSON."""
        geo = lambda g: mapping(g)
        req = json.loads(json.dumps(self.request, default=list))
        return dict(format=FORMAT, version=VERSION, units='mm', source=self.source, request=req,
                    stack=dict(layers=self.layers, copper=[dict(name=n, z=self.cu_z[n], t=self.cu_t[n]) for n in self.cu_order],
                               thickness=self.thickness),
                    outline=geo(self.outline), ground=self.ground, region=req['region'], cut_sides=self.cut_sides,
                    nets=None if self.sel is None else sorted(self.sel), ground_nets=sorted(self.ground_nets),
                    copper={l: [dict(net=n, polygon=geo(p)) for p, n in zip(ps, self.copper_nets.get(l, []))]
                            for l, ps in self.copper.items()},
                    vias=[dict(x=x, y=y, r=r, z0=z0, z1=z1, net=n) for (x, y, r, z0, z1), n in zip(self.vias, self.via_nets)],
                    ports=[p.to_dict() for p in self.ports], port_refs=sorted(self.port_refs),
                    parts=[p.to_dict() for p in self.parts],
                    walls=[dict(axis=a, at=c, t0=t0, t1=t1, z0=z0, z1=z1) for a, c, t0, t1, z0, z1 in self.walls],
                    prims=self.prims.to_dict(), notes=self.notes)

    def save(self, path):
        """Writes the board file (to_dict as JSON: <name>_board.json); returns path."""
        with open(path, 'w') as fh:
            json.dump(self.to_dict(), fh, default=_plain)
        return path

    @classmethod
    def from_dict(cls, d, log=print):
        if d.get('format') != FORMAT:
            raise ValueError(f"not a {FORMAT} file (format {d.get('format')!r})")
        if int(d.get('version', 0)) > VERSION:
            raise ValueError(f"board file version {d['version']} is newer than this program reads ({VERSION}): "
                             f"update kicad2ems")
        b = cls.__new__(cls)
        b.log, b.source, b.request, b.notes = log, d.get('source', {}), d.get('request', {}), list(d.get('notes', []))
        st = d['stack']
        b.layers, b.thickness = [dict(l) for l in st['layers']], st['thickness']
        b.cu_z = {c['name']: c['z'] for c in st['copper']}
        b.cu_t = {c['name']: c['t'] for c in st['copper']}
        b.outline, b.ground, b.region, b.cut_sides = shape(d['outline']), d.get('ground'), d.get('region'), d.get('cut_sides', [])
        b.sel = None if d.get('nets') is None else set(d['nets'])
        b.ground_nets = set(d.get('ground_nets', []))
        b.copper = {l: [shape(p['polygon']) for p in ps] for l, ps in d.get('copper', {}).items()}
        b.copper_nets = {l: [p.get('net', '') for p in ps] for l, ps in d.get('copper', {}).items()}
        b.vias = [(v['x'], v['y'], v['r'], v['z0'], v['z1']) for v in d.get('vias', [])]
        b.via_nets = [v.get('net', '') for v in d.get('vias', [])]
        b.ports = [Port.from_dict(p) for p in d.get('ports', [])]
        b.port_refs = set(d.get('port_refs', []))
        b.parts = [Part.from_dict(p) for p in d.get('parts', [])]
        b.walls = [(w['axis'], w['at'], w['t0'], w['t1'], w['z0'], w['z1']) for w in d.get('walls', [])]
        b.prims = Prims.from_dict(d.get('prims', {}))
        return b

    @classmethod
    def load(cls, path, log=print):
        with open(path) as fh:
            return cls.from_dict(json.load(fh), log=log)

    def summary(self, nd=4):
        """The numbers two boards (two importers, a refactor, a solver's readback) should agree on: the stack,
        copper area per layer and net, vias, ports, parts, edge walls, the outline (rounded to nd decimals)."""
        r = lambda v: round(float(v), nd)
        area = {}
        for l, ps in self.copper.items():
            for p, n in zip(ps, self.copper_nets.get(l, [''] * len(ps))):
                area.setdefault(l, {}).setdefault(n, 0.0)
                area[l][n] += p.area
        return dict(stack=[[l['name'], r(l['z0']), r(l['z1']), r(l['er']), r(l['tand'])] for l in self.layers],
                    copper_z={n: r(self.cu_z[n]) for n in self.cu_order}, ground=self.ground,
                    outline=dict(area=r(self.outline.area), bounds=[r(v) for v in self.outline.bounds]),
                    copper={l: {n: r(a) for n, a in sorted(v.items())} for l, v in sorted(area.items())},
                    vias=sorted([r(x), r(y), r(rv), r(z0), r(z1), n] for (x, y, rv, z0, z1), n in zip(self.vias, self.via_nets)),
                    ports=[dict(name=p.name, kind=p.kind, layer=p.layer, ref_layer=p.ref_layer, at=[r(v) for v in p.c],
                                axis=p.axis, sgn=p.sgn, w=r(p.w), z0=r(p.z0), net=p.net) for p in self.ports],
                    parts=[dict(ref=p.ref, value=p.value_str(), layer=p.layer, s=[r(v) for v in p.s], e=[r(v) for v in p.e])
                           for p in self.parts],
                    walls=[[a, r(c), r(t0), r(t1), r(z0), r(z1)] for a, c, t0, t1, z0, z1 in self.walls])


def _plain(v):
    """numpy values -> JSON."""
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (set, tuple)):
        return sorted(v) if isinstance(v, set) else list(v)
    try:
        return v.item()
    except AttributeError:
        return str(v)
