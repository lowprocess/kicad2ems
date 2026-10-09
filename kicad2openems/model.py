"""
The 3D model of a board for openEMS (units mm, y up = -y_KiCad):

  Model.layers   dielectric slabs (z0, z1, er, tand), bottom to top
  Model.cu_z     copper layer heights; Model.copper polygons per layer (selected nets)
  Model.outline  board outline (dielectric extent)
  Model.vias     (x, y, radius, z0, z1) metal cylinders
  Model.ports    lumped ports (Port) from the P<n> footprints; Model.user_ports groups the legs
                 of differential ports
  Model.parts    lumped R / L / C parts between two pads (parts.Part)
"""
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from .geometry import polygons, flip_y
from .kicad.outline import board_outline
from .kicad.nets import resolve_nets, DiffLegs
from .kicad.pnp import read_pos, pos_port_refs
from .parts import find_parts


def port_field(props, key, default=None):
    """Port footprint field: 'port.<key>' (preferred) or 'mom.<key>' (mom25d footprints)."""
    for k in (f'port.{key}', f'mom.{key}'):
        if props.get(k) not in (None, ''):
            return props[k]
    return default


class Port:
    def __init__(self, name, layer, z_sig, z_ref, c, axis, sgn, w, z0, leg=None, pair=None, ref_layer=''):
        self.name, self.layer, self.z_sig, self.z_ref = name, layer, z_sig, z_ref
        self.c, self.axis, self.sgn, self.w, self.z0 = np.asarray(c, float), axis, sgn, w, z0
        self.leg, self.pair, self.ref_layer = leg, pair, ref_layer

    def box(self):
        """Lumped-port sheet: across the trace, from reference copper to the trace."""
        a, b = self.axis, 1 - self.axis
        s, e = np.zeros(3), np.zeros(3)
        s[a] = e[a] = self.c[a]
        s[b], e[b] = self.c[b] - self.w / 2, self.c[b] + self.w / 2
        s[2], e[2] = self.z_ref, self.z_sig
        return s, e


class Model:
    """Geometry for openEMS from a KiCadBoard. Units mm, y up (y = -y_kicad)."""

    def __init__(self, board, nets=None, pos_file=None, include_mask=False, region=None, ground=None, parts='auto',
                 log=print):
        self.board, self.log = board, log
        self.notes = []
        # ---- stack, bottom to top ----
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
        # ---- outline / region ----
        ol = board_outline(board)
        self.sel = resolve_nets(nets, {n for _, n, _ in board.items} | {v['net'] for v in board.vias}) if nets else None
        cop = [g for l, n, g in board.items if l in self.cu_z and (self.sel is None or n in self.sel)]
        if ol is None:
            ol = unary_union(cop).envelope.buffer(1.0, join_style=2)
            self.notes.append("no Edge.Cuts outline: using the copper bounding box + 1 mm")
        if region:
            x0, y0, x1, y1 = region
            ol = ol.intersection(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        self.outline = flip_y(ol)
        if isinstance(self.outline, MultiPolygon):
            self.outline = max(self.outline.geoms, key=lambda p: p.area)
        self.outline = Polygon(self.outline.exterior)                         # dielectric: outer edge only
        # ---- copper per layer ----
        self.copper = {}
        for lay in self.cu_order:
            gs = [g for l, n, g in board.items if l == lay and (self.sel is None or n in self.sel)]
            if not gs:
                continue
            g = flip_y(unary_union(gs)).intersection(self.outline).simplify(2e-3, preserve_topology=True)
            ps = [orient(p, 1.0) for p in polygons(g) if p.area > 1e-6]
            if ps:
                self.copper[lay] = ps
        if ground:                                              # model this layer as a solid plane
            if ground not in self.cu_z:
                raise ValueError(f"ground layer {ground} is not a copper layer of the stackup")
            self.copper[ground] = [orient(self.outline, 1.0)]       # union of its copper and the outline = outline
            self.notes.append(f"{ground}: solid ground plane over the board outline (ground = {ground})")
        # ---- vias (metal cylinders) ----
        self.vias, self.via_nets = [], []
        for v in board.vias:
            if self.sel is not None and v['net'] not in self.sel:
                continue
            lys = [l for l in v['layers'] if l in self.cu_z]
            if len(lys) < 2:
                continue
            x, y = v['x'], -v['y']
            if not self.outline.buffer(1e-6).contains(Point(x, y)):
                continue
            zs = [self.cu_z[l] for l in lys]
            self.vias.append((x, y, v['drill'] / 2, min(zs), max(zs)))
            self.via_nets.append(v['net'])
        # ---- ports ----
        refs = None
        if pos_file:
            refs = pos_port_refs(read_pos(pos_file))
        self.port_fps = board.port_footprints(refs)
        if not self.port_fps:
            raise ValueError("no port footprints found (Fab text P<n>, or P<n> in the pick-and-place file)")
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
        # ---- lumped R / L / C parts ----
        self.parts = []
        if parts not in ('auto', 'none', None):
            raise ValueError(f"parts: 'auto' or 'none', not {parts!r}")
        if parts == 'auto':
            self.parts, notes = find_parts(board, self)
            self.notes += notes

    def __getstate__(self):
        """Pickled for parallel runs: the parsed board and the log callback stay behind."""
        st = dict(self.__dict__)
        st['board'], st['log'] = None, print
        return st

    def _copper_at(self, lay, pt):
        for p in self.copper.get(lay, []):
            if p.buffer(1e-6).contains(pt):
                return p
        return None

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
        cu = self._copper_at(lay, Point(*c))
        if cu is None:
            here = sorted({l for l in self.copper if self._copper_at(l, Point(*c)) is not None})
            raise ValueError(f"{name} ({fp['ref']}) at ({fp['x']:.3f}, {fp['y']:.3f}) mm is not on {lay} copper of the "
                             f"selected nets" + (f"; copper there on {here} - check the footprint field port.layer / mom.layer" if here else ''))
        # width across the feed at a point just inside the trace; centre the port on the trace
        t = np.array([-d[1], d[0]])
        probe = c + d * 0.05
        seg = LineString([probe - t * 20, probe + t * 20]).intersection(cu)
        parts = [seg] if seg.geom_type == 'LineString' else list(getattr(seg, 'geoms', []))
        part = min(parts, key=lambda q: q.distance(Point(*probe)))
        (x0, y0), (x1, y1) = part.coords[0], part.coords[-1]
        w = float(np.hypot(x1 - x0, y1 - y0))
        mid = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        delta = min(0.25 * w, 0.1)                                    # port plane just inside the trace end
        pc = c + d * delta
        pc[1 - axis] = mid[1 - axis]
        # reference: nearest copper below (top side) / above (bottom side) under the port
        zs = self.cu_z[lay]
        below = fp['side'] != 'B'
        ref = None
        for side in (below, not below):                         # preferred side first, then the other
            cand = [l for l in self.cu_order if (self.cu_z[l] < zs if side else self.cu_z[l] > zs)]
            cand.sort(key=lambda l: abs(self.cu_z[l] - zs))
            ref = next((l for l in cand if self._copper_at(l, Point(*pc)) is not None), None)
            if ref:
                break
        if ref is None:
            raise ValueError(f"{name}: no reference copper {'below' if below else 'above'} the port at "
                             f"({fp['x']:.3f}, {fp['y']:.3f}) mm - include the ground net in --nets")
        port = Port(name, lay, zs, self.cu_z[ref], pc, axis, sgn, w, float(port_field(props, 'z0', 50)), leg, pair, ref)
        kx, ky = pc[0], -pc[1]                                      # KiCad coordinates of the port point
        port.net = next((str(n) for l, n, g in self.board.items
                         if l == lay and n and g.buffer(1e-6).contains(Point(kx, ky))), None)
        return port

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
            L.append(f"  {p.name}: lumped, {p.layer} -> {p.ref_layer}, w {p.w:.3f} mm, h {abs(p.z_sig - p.z_ref):.3f} mm, "
                     f"at ({p.c[0]:.3f}, {-p.c[1]:.3f}) mm (KiCad), Z {p.z0:g} ohm")
        L += [p.describe() for p in self.parts]
        return "\n".join(L + self.notes)


OEModel = Model                     # backwards-compatible name
