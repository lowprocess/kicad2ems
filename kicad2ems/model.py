"""
The 3D model of a board for openEMS (units mm, y up = -y_KiCad):

  Model.layers   dielectric slabs (z0, z1, er, tand), bottom to top
  Model.cu_z     copper layer heights; Model.copper polygons per layer (selected nets)
  Model.outline  board outline (dielectric extent), cropped to the region if one is given
  Model.vias     (x, y, radius, z0, z1) metal cylinders
  Model.ports    lumped ports (Port) from the P<n> footprints inside the outline; Model.user_ports
                 groups the legs of differential ports
  Model.parts    lumped R / L / C parts between two pads (parts.Part)
"""
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from .geometry import polygons, flip_y
from .importers.kicad.outline import board_outline
from .importers.kicad.nets import resolve_nets, DiffLegs
from .importers.kicad.pnp import read_pos, pos_port_refs
from .parts import find_parts


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
    conductors on the same layer (centre-fed dipoles, gap feeds), from gap[0] to gap[1] along axis."""
    def __init__(self, name, layer, z_sig, z_ref, c, axis, sgn, w, z0, leg=None, pair=None, ref_layer='',
                 kind='z', gap=None, net=None):
        self.name, self.layer, self.z_sig, self.z_ref = name, layer, z_sig, z_ref
        self.c, self.axis, self.sgn, self.w, self.z0 = np.asarray(c, float), axis, sgn, w, z0
        self.leg, self.pair, self.ref_layer = leg, pair, ref_layer
        self.kind, self.gap, self.net = kind, gap, net
        self.source = ''                            # footprint TP1 / pad J1.1 / point (x, y) / cut (x, y)

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


class Model:
    """Geometry for openEMS from a KiCadBoard. Units mm, y up (y = -y_kicad)."""

    def __init__(self, board, nets=None, pos_file=None, include_mask=False, region=None, ground=None, parts='auto',
                 log=print, ports=None, cut_ports=False):
        from .region import region_polygon, auto_nets, extra_port_fps, ground_net_names
        self.board, self.log = board, log
        self.notes = []
        self._stack(board, include_mask)
        self.pos_file, self.port_specs, self.cut_ports = pos_file, list(ports or []), cut_ports
        # ---- outline / region (KiCad coordinates until flipped) ----
        full = board_outline(board)
        rpoly = region_polygon(board, region)
        if full is None:
            cop = [g for l, n, g in board.items if l in self.cu_z]
            full = unary_union(cop).envelope.buffer(1.0, join_style=2)
            self.notes.append("no Edge.Cuts outline: using the copper bounding box + 1 mm")
        # ---- nets: as given, all copper, or 'auto' (the nets under the ports + the ground nets) ----
        if nets in ('auto', ['auto'], ('auto',)):
            refs = pos_port_refs(read_pos(pos_file)) if pos_file else None
            fps = dict(board.port_footprints(refs))
            fps.update(extra_port_fps(board, self.port_specs))
            pts = []
            for fp in fps.values():
                for f in (fp.values() if isinstance(fp, DiffLegs) else [fp]):
                    lay = port_field(f['props'], 'layer') or ('B.Cu' if f['side'] == 'B' else 'F.Cu')
                    pts.append((lay, f['x'], f['y']))
            nets = auto_nets(board, pts, full)
            self.notes.append(f"nets (auto): {', '.join(nets)}")
        self.sel = resolve_nets(nets, {n for _, n, _ in board.items} | {v['net'] for v in board.vias}) if nets else None
        self.ground_nets = ground_net_names(board, full)
        ol = full
        self.region = region
        self.region_poly = rpoly
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
        self._kicad_outline = flip_y(self.outline)                            # the same in KiCad coordinates
        # ---- copper per layer: only items that can reach the outline ----
        keep = self._inside_items(board)
        self.copper = {}
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
        self._vias(board)
        self._ports(board, pos_file)
        # ---- lumped R / L / C parts ----
        self.parts = []
        if parts not in ('auto', 'none', None):
            raise ValueError(f"parts: 'auto' or 'none', not {parts!r}")
        if parts == 'auto':
            self.parts, notes = find_parts(board, self)
            self.notes += notes

    # ---- construction steps ------------------------------------------------------------------------
    def _stack(self, board, include_mask):
        items = [it for it in board.stack_items if it['kind'] in ('copper', 'diel') or
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
        self.cu_order = sorted(self.cu_z, key=lambda n: self.cu_z[n])         # bottom -> top

    def _inside_items(self, board):
        """Board items on copper layers of the stack whose bounding box meets the outline (KiCad
        coordinates): a cropped region of a large board never unions the copper it does not use."""
        cand = [(l, n, g) for l, n, g in board.items if l in self.cu_z]
        if not cand:
            return []
        tree = shapely.STRtree([g for _, _, g in cand])
        hit = tree.query(self._kicad_outline.buffer(1e-6))
        return [cand[i] for i in sorted(hit)]

    def _vias(self, board):
        self.vias, self.via_nets = [], []
        inside = self.outline.buffer(1e-6)
        for v in board.vias:
            if self.sel is not None and v['net'] not in self.sel:
                continue
            lys = [l for l in v['layers'] if l in self.cu_z]
            if len(lys) < 2:
                continue
            x, y = v['x'], -v['y']
            if not inside.contains(Point(x, y)):
                continue
            zs = [self.cu_z[l] for l in lys]
            self.vias.append((x, y, v['drill'] / 2, min(zs), max(zs)))
            self.via_nets.append(v['net'])

    def _ports(self, board, pos_file):
        from .region import extra_port_fps, cut_port_fps
        refs = pos_port_refs(read_pos(pos_file)) if pos_file else None
        fps = dict(board.port_footprints(refs))
        for k, f in fps.items():
            for leg in (f.values() if isinstance(f, DiffLegs) else [f]):
                leg.setdefault('source', f"footprint {leg['ref']}")
        for name, fp in self._extra_ports(board).items():      # ports given in the settings (pad / point)
            n, leg = name
            if leg:
                fps.setdefault(n, DiffLegs())[leg] = fp
            else:
                fps[n] = fp
        if self.cut_ports and self.region_poly is not None:
            signal = (set(self.sel) - self.ground_nets) if self.sel is not None else \
                {self._net_at_kicad(f) for f in self._single_legs(fps)} - {None}
            cut, notes = cut_port_fps(board, self.region_poly, signal, set(self.cu_z), first=max(fps, default=0) + 1)
            fps.update(cut)
            self.notes += notes
            if cut:
                self.notes.append(f"{len(cut)} port(s) where tracks cross the region edge: "
                                  f"{', '.join(f'P{n}' for n in cut)}")
        if not fps:
            raise ValueError("no ports: no port footprints (Fab text P<n>, or P<n> in the pick-and-place file), "
                             "no ports in the settings and no cut ports")
        inside = self.outline.buffer(1e-6)
        self.port_refs = {f['ref'] for fp in fps.values() for f in (fp.values() if isinstance(fp, DiffLegs) else [fp])}
        self.port_fps = {}
        for n, fp in fps.items():
            legs = fp.values() if isinstance(fp, DiffLegs) else [fp]
            out = [f for f in legs if not inside.contains(Point(f['x'], -f['y']))]
            if out:
                self.notes.append(f"P{n} ({', '.join(f['ref'] for f in out)}) is outside the "
                                  f"{'region' if self.region else 'board outline'}: left out")
                continue
            self.port_fps[n] = fp
        if not self.port_fps:
            raise ValueError("no port footprints inside the " + ("region" if self.region else "board outline"))
        self.ports, self.user_ports = [], []
        for n, fp in self.port_fps.items():
            if isinstance(fp, DiffLegs):
                pp = self._port(f"P{n}+", fp['+'], leg='+', pair=f"P{n}")
                pn = self._port(f"P{n}-", fp['-'], leg='-', pair=f"P{n}")
                self.ports += [pp, pn]
                self.user_ports.append((f"P{n}", [pp, pn]))
            else:
                p = self._port(f"P{n}", fp)
                self.ports.append(p)
                self.user_ports.append((p.name, [p]))
        # signal nets: the nets of the traces the ports sit on; vias on them are 'signal vias'
        self.signal_nets = {p.net for p in self.ports if p.net}
        self.via_signal = [n in self.signal_nets for n in self.via_nets]

    def __getstate__(self):
        """Pickled for parallel runs: the parsed board and the log callback stay behind."""
        st = dict(self.__dict__)
        st['board'], st['log'] = None, print
        return st

    def _extra_ports(self, board):
        from .region import extra_port_fps, parse_port_spec
        out = {}
        legs = {int(''.join(ch for ch in parse_port_spec(sp)[0] if ch.isdigit())): parse_port_spec(sp)[0]
                for sp in self.port_specs}
        for n, fp in extra_port_fps(board, self.port_specs).items():
            name = legs.get(n, f"P{n}")
            out[(n, '+' if name.endswith('+') else '-' if name.endswith('-') else '')] = fp
        return out

    @staticmethod
    def _single_legs(fps):
        for f in fps.values():
            yield from (f.values() if isinstance(f, DiffLegs) else [f])

    def _net_at_kicad(self, f):
        from .region import net_at
        lay = port_field(f['props'], 'layer') or ('B.Cu' if f['side'] == 'B' else 'F.Cu')
        return net_at(self.board, lay, f['x'], f['y'])

    # ---- helpers -----------------------------------------------------------------------------------------
    def copper_at(self, lay, pt):
        """The modelled copper polygon on layer `lay` containing point pt (model coordinates), or None."""
        for p in self.copper.get(lay, []):
            if p.distance(pt) < 1e-6:
                return p
        return None

    def _net_at(self, lay, x, y):
        """Net of the board copper on layer `lay` at model point (x, y)."""
        pt = Point(x, -y)
        for l, n, g in self.board.items:
            if l == lay and n and g.distance(pt) < 1e-6:
                if self.sel is None or n in self.sel:
                    return str(n)
        return None

    def _reference_hint(self):
        if self.sel is not None:
            return "include the ground net in nets, or set ground = <layer>"
        return "no copper on any layer there: the board needs a reference plane, or set ground = <layer>"

    def _port(self, name, fp, leg=None, pair=None):
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
        cu = self.copper_at(lay, Point(*c))
        if cu is None and str(port_field(props, 'type', '')).lower() != 'z':
            gp = self._gap_port(name, fp, lay, c, d, axis, sgn, props, leg, pair)
            if gp is not None:
                return gp
        if cu is None:
            here = sorted({l for l in self.copper if self.copper_at(l, Point(*c)) is not None})
            raise ValueError(f"{name} ({fp['ref']}) at ({fp['x']:.3f}, {fp['y']:.3f}) mm is not on {lay} copper of the "
                             f"selected nets" + (f"; copper there on {here} - check the footprint field port.layer / "
                                                 f"mom.layer" if here else ''))
        # width across the feed at a point just inside the trace; centre the port on the trace
        w, mid = cross_section(cu, c + d * 0.05, np.array([-d[1], d[0]]))
        pc = c + d * min(0.25 * w, 0.1)                               # port plane just inside the trace end
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
        port = Port(name, lay, zs, self.cu_z[ref], pc, axis, sgn, w, float(port_field(props, 'z0', 50)), leg, pair, ref,
                    net=self._net_at(lay, *pc))
        port.source = fp.get('source', f"footprint {fp['ref']}")
        return port

    def _gap_port(self, name, fp, lay, c, d, axis, sgn, props, leg, pair, reach=5.0):
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
                    kind='gap', gap=(min(ends), max(ends)))

    def describe(self):
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
