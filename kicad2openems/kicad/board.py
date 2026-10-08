"""
KiCadBoard: reads a .kicad_pcb file (KiCad 5 to 10).

  stackup     Board Setup > Physical Stackup: copper / dielectric order, thickness, er, loss
              tangent, solder mask; dielectric sub-layers are split
  copper      tracks, arcs, vias (through / blind / micro), filled zones (the real copper,
              antipads included), footprint pads (incl. custom), plated through-holes (as vias),
              gr_poly / gr_rect / gr_circle on copper layers
  ports       footprints marked P<n> (Fab text, or Reference / Value when listed in a pick-and-place
              file); P<n>+ / P<n>- (or P<n>P / P<n>N) mark the legs of a differential port

Items are kept in KiCad coordinates (mm, y down) as shapely geometry: board.items is a list of
(layer, net, geometry), board.vias a list of dicts, board.footprints a list of dicts.
"""
import re
import numpy as np
from shapely.geometry import Polygon, LineString, Point, box
from . import sexpr as sx
from . import shapes as shp
from .nets import DiffLegs
from .sexpr import parse_sexpr

MM = 1e-3                                  # stackup thicknesses are kept in metres


class KiCadBoard:
    def __init__(self, path):
        self.path = path
        with open(path, encoding='utf-8') as fh:
            self.root = parse_sexpr(fh.read())
        assert self.root[0] == 'kicad_pcb', "not a .kicad_pcb file"
        self._nets()
        self._stackup()
        self._copper()

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
            self.has_stackup = False
            return
        self.has_stackup = True
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

    def describe_stackup(self):
        out = []
        for it in self.stack_items:
            if it['kind'] == 'copper':
                out.append(f"  {it['name']:<14s} copper   {it['t'] * 1e6:6.1f} um")
            else:
                out.append(f"  {it['name']:<14s} {it['kind']:<8s} {it['t'] * 1e3:6.3f} mm  er {it['er']:.2f}  tand {it['tand']:.4f}")
        return "\n".join(out)

    # ---- copper -----------------------------------------------------------------------------
    def _copper(self):
        """Collect copper items as (layer, net, geometry-in-KiCad-mm) plus vias and port footprints."""
        self.items = []          # (layer, net, shapely geometry (mm, y down))
        self.vias = []           # dict(x, y, drill, size, layers, net)
        self.footprints = []
        all_cu = self.copper_order

        def expand(lys):
            out = []
            for l in lys:
                if l in ('*.Cu', 'F&B.Cu'):
                    out += all_cu if l == '*.Cu' else ['F.Cu', 'B.Cu']
                elif l.endswith('.Cu'):
                    out.append(l)
            return out

        for s in sx.items(self.root, 'segment'):
            g = LineString([sx.xy(s, 'start'), sx.xy(s, 'end')]).buffer(sx.val(s, 'width', 0.2, float) / 2, quad_segs=6)
            for l in expand(sx.layers(s)):
                self.items.append((l, self._net_of(s), g))
        for a in sx.items(self.root, 'arc'):
            pts = shp.arc_points(sx.xy(a, 'start'), sx.xy(a, 'mid'), sx.xy(a, 'end'))
            g = LineString(pts).buffer(sx.val(a, 'width', 0.2, float) / 2, quad_segs=6)
            for l in expand(sx.layers(a)):
                self.items.append((l, self._net_of(a), g))
        for v in sx.items(self.root, 'via'):
            x, y = sx.xy(v, 'at')
            lys = expand(sx.layers(v))
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
                pts = [(float(p[1]), float(p[2])) for p in sx.item(fp, 'pts')[1:] if p[0] == 'xy']
                if len(pts) >= 3:
                    self.items.append((lay, net, Polygon(pts).buffer(0)))
        for tag in ('gr_poly', 'gr_rect', 'gr_circle'):
            for g in sx.items(self.root, tag):
                lys = [l for l in sx.layers(g) if l.endswith('.Cu')]
                if not lys:
                    continue
                if tag == 'gr_poly':
                    geom = Polygon([(float(p[1]), float(p[2])) for p in sx.item(g, 'pts')[1:] if p[0] == 'xy'])
                elif tag == 'gr_rect':
                    (x0, y0), (x1, y1) = sx.xy(g, 'start'), sx.xy(g, 'end')
                    geom = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                else:
                    c, e = np.array(sx.xy(g, 'center')), np.array(sx.xy(g, 'end'))
                    geom = Point(*c).buffer(np.linalg.norm(e - c), quad_segs=12)
                for l in lys:
                    self.items.append((l, self._net_of(g), geom))
        for fp in sx.items(self.root, 'footprint') + sx.items(self.root, 'module'):
            self._footprint(fp, expand)

    def _footprint(self, fp, expand):
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
            lys = expand(sx.layers(pad))
            net = self._net_of(pad)
            for l in lys:
                self.items.append((l, net, geom))
            drill = sx.item(pad, 'drill')
            if pad[2] == 'thru_hole' and drill and len(lys) > 1:
                d = float(drill[1]) if len(drill) > 1 and drill[1] != 'oval' else float(drill[2])
                self.vias.append(dict(x=centre[0], y=centre[1], size=0, drill=d, layers=lys, net=net, pth=True))
            pads.append(dict(name=pad[1], xy=centre, layers=lys, net=net, geom=geom))
        self.footprints.append(dict(ref=ref, value=val, fab=fab_texts, props=props, x=org[0], y=org[1],
                                    rot=rot, side=side, lib=str(fp[1]), pads=pads))

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

    # ---- model -------------------------------------------------------------------------------
