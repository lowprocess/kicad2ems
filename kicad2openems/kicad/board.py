"""
KiCadBoard: reads a .kicad_pcb file (KiCad 5 to 10).

  stackup     Board Setup > Physical Stackup: copper / dielectric order, thickness, er, loss
              tangent, solder mask; dielectric sub-layers are split
  copper      tracks, arcs, vias (through / blind / micro), filled zones (the real copper,
              antipads included), footprint pads (incl. custom, chamfered, trapezoid), copper
              graphics on the board (gr_line / gr_arc / gr_rect / gr_circle / gr_poly / gr_curve)
              and inside footprints (fp_*), plated through-holes (as vias)
  outline     every Edge.Cuts item, on the board and in footprints (board.edges)
  ports       footprints marked P<n> (Fab text, or Reference / Value when listed in a pick-and-place
              file); P<n>+ / P<n>- (or P<n>P / P<n>N) mark the legs of a differential port

Items are kept in KiCad coordinates (mm, y down) as shapely geometry: board.items is a list of
(layer, net, geometry), board.tracks the routed tracks (for transmission-line sections), board.vias a
list of dicts, board.footprints a list of dicts. Not read: zones defined inside footprints.
"""
import re
import numpy as np
from shapely.geometry import Polygon, LineString, Point
from . import sexpr as sx
from . import shapes as shp
from .nets import DiffLegs
from .sexpr import parse_sexpr

MM = 1e-3                                  # stackup thicknesses are kept in metres
GRAPHICS = ('line', 'arc', 'rect', 'circle', 'poly', 'curve')


class KiCadBoard:
    def __init__(self, path=None, text=None):
        """From a file, or from the text of one (KiCad's API returns the open board as text)."""
        self.path = path or ''
        if text is None:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        self.root = parse_sexpr(text)
        if not (isinstance(self.root, list) and self.root and self.root[0] == 'kicad_pcb'):
            raise ValueError(f"{path or 'board text'}: not a .kicad_pcb file")
        self._nets()
        self._stackup()
        self._copper()

    @classmethod
    def from_text(cls, text, path=''):
        return cls(path=path, text=text)

    # ---- nets ---------------------------------------------------------------------------
    def _nets(self):
        self.net_names = {}
        for n in sx.items(self.root, 'net'):
            if len(n) >= 3:
                self.net_names[str(n[1])] = n[2]

    def _net_of(self, node):
        it = sx.item(node, 'net')
        if not it or len(it) < 2:
            return ''
        if len(it) >= 3:
            return it[2]
        v = str(it[1])
        return self.net_names.get(v, v)

    # ---- stackup ---------------------------------------------------------------------------
    def _stackup(self):
        setup = sx.item(self.root, 'setup')
        su = sx.item(setup, 'stackup') if setup else None
        self.stack_items = []
        self.copper_order = [str(l[1]) for l in (sx.item(self.root, 'layers') or [])[1:]
                             if isinstance(l, list) and len(l) > 1 and str(l[1]).endswith('.Cu')]
        if su is None:
            return
        for l in sx.items(su, 'layer'):
            name, typ = l[1], (sx.val(l, 'type', '') or '').lower()
            if typ == 'copper':
                self.stack_items.append(dict(kind='copper', name=name, t=sx.val(l, 'thickness', 0.035, float) * MM))
            elif 'mask' in typ:
                self.stack_items.append(dict(kind='mask', name=name, t=sx.val(l, 'thickness', 0.01, float) * MM,
                                             er=sx.val(l, 'epsilon_r', 3.3, float),
                                             tand=sx.val(l, 'loss_tangent', 0.0, float)))
            elif typ in ('core', 'prepreg') or 'dielectric' in str(name).lower():
                # split sub-layers: a flat list (thickness, material, epsilon_r, loss_tangent)*
                subs, cur = [], {}
                for c in l[2:]:
                    if not isinstance(c, list):
                        continue
                    if c[0] == 'addsublayer':
                        subs.append(cur); cur = {}
                    elif c[0] in ('thickness', 'epsilon_r', 'loss_tangent'):
                        cur[c[0]] = float(c[1])
                subs.append(cur)
                for s in subs:
                    self.stack_items.append(dict(kind='diel', name=name, t=s.get('thickness', 0.1) * MM,
                                                 er=s.get('epsilon_r', 4.5), tand=s.get('loss_tangent', 0.02)))

    # ---- copper -----------------------------------------------------------------------------
    def _expand(self, lys):
        out = []
        for l in lys:
            if l in ('*.Cu', 'F&B.Cu'):
                out += self.copper_order if l == '*.Cu' else ['F.Cu', 'B.Cu']
            elif l.endswith('.Cu'):
                out.append(l)
        return out

    def _copper(self):
        """Collect copper items as (layer, net, geometry-in-KiCad-mm) plus tracks, vias, footprints and
        the Edge.Cuts outline pieces."""
        self.items = []          # (layer, net, shapely geometry (mm, y down))
        self.shapes = []         # the zones and drawn copper among items (not tracks, vias or pads)
        self.tracks = []         # dict(layer, net, width, pts (polyline, mm, y down))
        self.vias = []           # dict(x, y, drill, size, layers, net)
        self.footprints = []
        self.edges = []          # Edge.Cuts outline pieces (LineString / LinearRing, mm, y down)
        self.drawings = {}       # other non-copper layers: {layer: [outline pieces]} (regions on User.1 ...)
        all_cu = self.copper_order
        for s in sx.items(self.root, 'segment'):
            pts = [sx.xy(s, 'start'), sx.xy(s, 'end')]
            self._track(s, pts)
        for a in sx.items(self.root, 'arc'):
            self._track(a, shp.arc_points(sx.xy(a, 'start'), sx.xy(a, 'mid'), sx.xy(a, 'end')))
        for v in sx.items(self.root, 'via'):
            x, y = sx.xy(v, 'at')
            lys = self._expand(sx.layers(v))
            kind = 'micro' if 'micro' in v else ('blind' if 'blind' in v else 'through')
            if kind == 'through' and len(lys) == 2 and all_cu:
                i0, i1 = sorted(all_cu.index(l) for l in lys)
                lys = all_cu[i0:i1 + 1]
            size = sx.val(v, 'size', 0.6, float); drill = sx.val(v, 'drill', 0.3, float)
            self.vias.append(dict(x=x, y=y, size=size, drill=drill, layers=lys, net=self._net_of(v)))
            for l in lys:
                self.items.append((l, self._net_of(v), Point(x, y).buffer(size / 2, quad_segs=6)))
        for z in sx.items(self.root, 'zone'):
            net = sx.val(z, 'net_name', '') or self._net_of(z)
            for fp in sx.items(z, 'filled_polygon'):
                lay = sx.val(fp, 'layer')
                pts = sx.pts(fp)
                if len(pts) >= 3:
                    self.items.append((lay, net, Polygon(pts).buffer(0)))
                    self.shapes.append(self.items[-1])
        for kind in GRAPHICS:
            for g in sx.items(self.root, 'gr_' + kind):
                lys = sx.layers(g)
                if 'Edge.Cuts' in lys:
                    ring = shp.outline_ring(g)
                    if ring is not None:
                        self.edges.append(ring)
                for l in lys:
                    if l != 'Edge.Cuts' and not l.endswith('.Cu') and not l.endswith('.Mask'):
                        ring = shp.outline_ring(g)
                        if ring is not None:
                            self.drawings.setdefault(l, []).append(ring)
                cu = [l for l in lys if l.endswith('.Cu')]
                geom = shp.graphic(g) if cu else None
                if geom is not None and not geom.is_empty:
                    for l in cu:
                        self.items.append((l, self._net_of(g), geom))
                        self.shapes.append(self.items[-1])
        for fp in sx.items(self.root, 'footprint') + sx.items(self.root, 'module'):
            self._footprint(fp)

    def _track(self, node, pts):
        w = sx.val(node, 'width', 0.2, float)
        net = self._net_of(node)
        g = LineString(pts).buffer(w / 2, quad_segs=6)
        for l in self._expand(sx.layers(node)):
            self.items.append((l, net, g))
            self.tracks.append(dict(layer=l, net=net, width=w, pts=np.asarray(pts, float)))

    def _footprint(self, fp):
        at = sx.item(fp, 'at')
        org = (float(at[1]), float(at[2]))
        rot = float(at[3]) if len(at) > 3 else 0.0
        ref, val, fab_texts, props = '', '', [], {}
        for p in sx.items(fp, 'property'):
            props[p[1]] = p[2] if len(p) > 2 else ''
            if any(str(l).endswith('.Fab') for l in sx.layers(p)) and len(p) > 2:
                fab_texts.append(p[2])
        for t in sx.items(fp, 'fp_text'):
            kind, txt = t[1], t[2]
            if kind == 'reference':
                ref = txt
            elif kind == 'value':
                val = txt
            if any(str(l).endswith('.Fab') for l in sx.layers(t)):
                fab_texts.append(txt)
        ref = props.get('Reference', ref); val = props.get('Value', val)
        side = 'B' if sx.val(fp, 'layer', 'F.Cu').startswith('B') else 'F'
        pads = []
        for pad in sx.items(fp, 'pad'):
            pat = sx.item(pad, 'at')
            pxy = np.array([float(pat[1]), float(pat[2])])
            prot = float(pat[3]) if len(pat) > 3 else 0.0
            if fp[0] == 'module':                         # KiCad 5: pad angle relative
                prot += rot
            centre = np.array(org) + shp.rot(pxy, rot)
            geom = shp.place(shp.pad_shape(pad), centre, prot)
            lys = self._expand(sx.layers(pad))
            net = self._net_of(pad)
            for l in lys:
                self.items.append((l, net, geom))
            drill = sx.item(pad, 'drill')
            if pad[2] == 'thru_hole' and drill and len(lys) > 1:
                d = float(drill[1]) if len(drill) > 1 and drill[1] != 'oval' else float(drill[2])
                self.vias.append(dict(x=centre[0], y=centre[1], size=0, drill=d, layers=lys, net=net, pth=True))
            pads.append(dict(name=pad[1], xy=centre, layers=lys, net=net, geom=geom, rot=prot, ref=ref))
        # graphics: copper shapes (net of the pad they touch on that layer) and Edge.Cuts pieces
        for kind in GRAPHICS:
            for g in sx.items(fp, 'fp_' + kind):
                lys = sx.layers(g)
                if 'Edge.Cuts' in lys:
                    ring = shp.outline_ring(g)
                    if ring is not None:
                        self.edges.append(shp.place(ring, org, rot))
                cu = [l for l in self._expand(lys)]
                local = shp.graphic(g) if cu else None
                if local is None or local.is_empty:
                    continue
                geom = shp.place(local, org, rot)
                for l in cu:
                    net = next((p['net'] for p in pads if l in p['layers'] and p['geom'].intersects(geom)), '')
                    self.items.append((l, net, geom))
                    self.shapes.append(self.items[-1])
        attr = [str(t) for t in (sx.item(fp, 'attr') or [])[1:]]
        flag = lambda k: k in attr or sx.val(fp, k, 'no') == 'yes'
        self.footprints.append(dict(ref=ref, value=val, fab=fab_texts, props=props, x=org[0], y=org[1],
                                    rot=rot, side=side, lib=str(fp[1]), pads=pads, dnp=flag('dnp'),
                                    exclude_from_sim=flag('exclude_from_sim')))

    # ---- ports ----------------------------------------------------------------------------
    def port_footprints(self, pos_refs=None):
        """{n: footprint} for single-ended ports (Fab text P<n>), {n: {'+': fp, '-': fp}} for
        differential ports (Fab text P<n>+ / P<n>- or P<n>P / P<n>N). Reference/Value are used for
        refs listed in a pick-and-place file."""
        pat = re.compile(r'^P(\d+)([+\-PN])?$', re.I)
        out = {}

        def put(m, fp):
            n, leg = int(m.group(1)), (m.group(2) or '').upper()
            if leg:
                out.setdefault(n, DiffLegs())['+' if leg in ('+', 'P') else '-'] = fp
            else:
                out[n] = fp
        for fp in self.footprints:
            done = False
            for t in fp['fab']:
                m = pat.match(str(t).strip())
                if m:
                    put(m, fp); done = True; break
            if not done and pos_refs is not None and fp['ref'] in pos_refs:
                for t in (fp['value'], fp['ref'], *fp['fab']):
                    m = pat.match(str(t).strip())
                    if m:
                        put(m, fp); break
        for n, v in out.items():
            if isinstance(v, DiffLegs) and set(v) != {'+', '-'}:
                raise ValueError(f"differential port P{n} needs both P{n}+ and P{n}- footprints")
        return dict(sorted(out.items()))
