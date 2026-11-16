"""
KiCadBoard: reads a .kicad_pcb file (KiCad 5 to 10).

  stackup     Board Setup > Physical Stackup: copper / dielectric order, thickness, er, loss
              tangent, solder mask; dielectric sub-layers are split
  copper      tracks, arcs, vias (through / blind / micro), filled zones (the real copper,
              antipads included), footprint pads (incl. custom, chamfered, trapezoid), copper
              graphics on the board (gr_line / gr_arc / gr_rect / gr_circle / gr_poly / gr_curve)
              and inside footprints (fp_*), plated through-holes (as vias), copper text (outlined by KiCad:
              exact; in its stroke font: a stand-in font in the text's box, said in the notes), KiCad 9+
              padstacks (a pad's or via's shape on each copper layer), and pads and vias that remove their
              unused layers (their copper only where something connects, or at their ends if kept)
  outline     every Edge.Cuts item, on the board and in footprints (board.edges)
  ports       footprints marked P<n> (Fab text, or Reference / Value when listed in a pick-and-place
              file); P<n>+ / P<n>- (or P<n>P / P<n>N) mark the legs of a differential port

The board is the raw form of importers/ (Imported): KiCad coordinates (mm, y down), shapely geometry;
items (layer, net, geometry), tracks (for transmission-line sections), vias, footprints. Not read: zones
defined inside footprints.
"""
import math
import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, LineString, Point
from .. import Imported
from . import sexpr as sx
from . import shapes as shp
from .sexpr import parse_sexpr

MM = 1e-3                                  # stackup thicknesses are kept in metres
GRAPHICS = ('line', 'arc', 'rect', 'circle', 'poly', 'curve')


class KiCadBoard(Imported):
    importer = 'kicad'

    def __init__(self, path=None, text=None):
        """From a file, or from the text of one (KiCad's API returns the open board as text)."""
        super().__init__(path or '')
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
        self.net_names = {str(n[1]): n[2] for n in sx.items(self.root, 'net') if len(n) >= 3}

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
        self.copper_order = [str(l[1]) for l in (sx.item(self.root, 'layers') or [])[1:]
                             if isinstance(l, list) and len(l) > 1 and str(l[1]).endswith('.Cu')]
        if su is None:
            return
        self.copper_finish = sx.val(su, 'copper_finish', None)      # KiCad's name ('ENIG', 'HAL lead-free', ...)
        for l in sx.items(su, 'layer'):
            name, typ = l[1], (sx.val(l, 'type', '') or '').lower()
            if typ == 'copper':
                self.stack_items.append(dict(kind='copper', name=name, t=sx.val(l, 'thickness', 0.035, float) * MM))
            elif 'mask' in typ:
                self.stack_items.append(dict(kind='mask', name=name, t=sx.val(l, 'thickness', 0.01, float) * MM,
                                             er=sx.val(l, 'epsilon_r', 3.3, float),
                                             tand=sx.val(l, 'loss_tangent', 0.0, float),
                                             material=sx.val(l, 'material', None),
                                             given=[k for k in ('epsilon_r', 'loss_tangent') if sx.item(l, k)]))
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
                    elif c[0] == 'material' and len(c) > 1:
                        cur['material'] = str(c[1])
                subs.append(cur)
                for s in subs:                              # (er / tand None: not in the file, Board fills them)
                    self.stack_items.append(dict(kind='diel', name=name, t=s.get('thickness', 0.1) * MM,
                                                 er=s.get('epsilon_r'), tand=s.get('loss_tangent'),
                                                 material=s.get('material')))

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
        the Edge.Cuts outline pieces (and the outlines drawn on other layers: regions on User.1 ...)."""
        all_cu = self.copper_order
        self._removable, self._stroke_text = [], 0
        for s in sx.items(self.root, 'segment'):
            self._track(s, [sx.xy(s, 'start'), sx.xy(s, 'end')])
        for a in sx.items(self.root, 'arc'):
            self._track(a, shp.arc_points(sx.xy(a, 'start'), sx.xy(a, 'mid'), sx.xy(a, 'end')))
        for v in sx.items(self.root, 'via'):
            x, y = sx.xy(v, 'at')
            lys = self._expand(sx.layers(v))
            kind = 'micro' if 'micro' in v else ('blind' if 'blind' in v else 'through')
            ends = lys
            if len(lys) == 2 and all_cu and all(l in all_cu for l in lys):     # every copper layer from one end
                i0, i1 = sorted(all_cu.index(l) for l in lys)                  # to the other, a blind or micro via's
                lys = all_cu[i0:i1 + 1]                                        # too: its barrel passes them
            pads = lys if kind == 'through' else ends                          # (its rings: at its ends, as HDI
                                                                               # boards' unused layers are removed)
            size, drill = sx.val(v, 'size', 0.6, float), sx.val(v, 'drill', 0.3, float)
            self.vias.append(dict(x=x, y=y, size=size, drill=drill, layers=lys, net=self._net_of(v), kind=kind))
            for l in pads:
                e = self._stack_entry(v, l)                                    # (KiCad 9+: its size on l)
                d = sx.val(e, 'size', size, float) if e is not None else size
                self.items.append((l, self._net_of(v), Point(x, y).buffer(d / 2, quad_segs=6)))
                self._unused(len(self.items) - 1, v, ends)
        for z in sx.items(self.root, 'zone'):
            net = sx.val(z, 'net_name', '') or self._net_of(z)
            if net:
                self.zone_nets.add(net)
            fills = sx.items(z, 'filled_polygon')
            if not fills and sx.item(z, 'polygon') is not None and not sx.item(z, 'keepout'):
                self.notes.append(f"a zone of {net or 'no net'} on {', '.join(sx.layers(z)) or sx.val(z, 'layer', '?')} "
                                  f"is not filled: left out (fill the zones in KiCad, B, and save)")
            for fp in fills:
                lay = sx.val(fp, 'layer') or sx.val(z, 'layer')    # (KiCad 5: the zone's own layer)
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
        for g in sx.items(self.root, 'gr_text'):
            self._text(g, g[1] if len(g) > 1 else '')
        for g in sx.items(self.root, 'gr_text_box') + sx.items(self.root, 'gr_textbox'):
            if any(str(l).endswith('.Cu') for l in sx.layers(g)):
                self.notes.append(f"a text box on {', '.join(l for l in sx.layers(g) if l.endswith('.Cu'))}: left out")
        for fp in sx.items(self.root, 'footprint') + sx.items(self.root, 'module'):
            self._footprint(fp)
        if self._stroke_text:
            self.notes.append(f"{self._stroke_text} copper text(s) in KiCad's stroke font: drawn from a stand-in "
                              f"font in each text's box, so their copper is approximate (outlined text is exact)")
        self._drop_unused()

    def _stack_entry(self, node, layer):
        """The padstack entry (KiCad 9+) that sets node's shape on `layer`, or None (the pad's own: F.Cu, and
        every layer in the normal mode). front_inner_back: 'Inner' for the inner layers and 'B.Cu'; custom: each
        layer by name."""
        stack = sx.item(node, 'padstack')
        if stack is None:
            return None
        mode = sx.val(stack, 'mode', 'normal')
        want = layer if mode == 'custom' else ('B.Cu' if layer == 'B.Cu' else
                                               ('Inner' if layer != 'F.Cu' and mode == 'front_inner_back' else None))
        if want is None:
            return None
        return next((e for e in sx.items(stack, 'layer') if len(e) > 1 and e[1] == want), None)

    @staticmethod
    def _flag(node, name):
        """A yes/no flag: (name yes) in KiCad 8+, a bare (name) before."""
        it = sx.item(node, name)
        return it is not None and (len(it) < 2 or str(it[1]) == 'yes')

    def _unused(self, idx, node, ends):
        """Mark self.items[idx] (a pad's or via's copper on one layer) as removable where it connects nothing:
        remove_unused_layers; its end layers kept with keep_end_layers, the zone_layer_connections' kept."""
        if not self._flag(node, 'remove_unused_layers'):
            return
        lay = self.items[idx][0]
        zones = [str(x) for x in (sx.item(node, 'zone_layer_connections') or [])[1:]]
        if (self._flag(node, 'keep_end_layers') and lay in ends) or lay in zones:
            return
        self._removable.append(idx)

    def _drop_unused(self):
        """Take out the removable pad and via copper (_unused) that no other copper of its net touches on
        its layer: KiCad draws none there."""
        if not self._removable:
            return
        from shapely.strtree import STRtree
        cand = set(self._removable)
        layers = {}
        for i in self._removable:
            layers.setdefault(self.items[i][0], []).append(i)
        touched = set()
        for lay, rings in layers.items():                       # the rings in a tree, each other piece of copper of
            tree = STRtree([self.items[i][2] for i in rings])   # the layer asked once (a fill prepared once: it
            others = [j for j, it in enumerate(self.items)      # had been asked of each ring, 16 s on vme-wren)
                      if it[0] == lay and j not in cand and it[1]]
            if not others:
                continue
            src, hit = tree.query([self.items[j][2] for j in others], predicate='intersects')
            for s, h in zip(src, hit):
                i = rings[h]
                if self.items[others[s]][1] == self.items[i][1]:
                    touched.add(i)
        drop = cand - touched
        if drop:
            self.items = [it for i, it in enumerate(self.items) if i not in drop]
            self.shapes = [s for s in self.shapes if any(s is it for it in self.items)]
            self.notes.append(f"{len(drop)} pad and via rings left off the layers they connect nothing on "
                              f"(remove_unused_layers)")

    def _text(self, node, text, origin=(0.0, 0.0), deg=0.0, net=''):
        """A text on copper: its outline into self.items (layer, net, geometry); hidden text is none."""
        cu = self._expand([x for x in sx.layers(node) if x != 'knockout'])
        if not cu or not str(text).strip() or shp.text_hidden(node):
            return
        geom, exact = shp.text_shape(node, text, origin, deg)
        if geom.is_empty:
            return
        for l in cu:
            self.items.append((l, net, geom))
            self.shapes.append(self.items[-1])
        if not exact:
            self._stroke_text += 1

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
            if len(p) > 2:
                if any(str(l).endswith('.Fab') for l in sx.layers(p)):
                    fab_texts.append(p[2])
                self._text(p, p[2], org, rot)                               # (on copper: copper)
        for t in sx.items(fp, 'fp_text'):
            if len(t) > 2:
                self._text(t, t[2], org, rot)
            kind, txt = t[1], t[2]
            if kind == 'reference':
                ref = txt
            elif kind == 'value':
                val = txt
            if any(str(l).endswith('.Fab') for l in sx.layers(t)):
                fab_texts.append(txt)
        ref, val = props.get('Reference', ref), props.get('Value', val)
        side = 'B' if sx.val(fp, 'layer', 'F.Cu').startswith('B') else 'F'
        pads = []
        for pad in sx.items(fp, 'pad'):
            pat = sx.item(pad, 'at')
            pxy = np.array([float(pat[1]), float(pat[2])])
            prot = float(pat[3]) if len(pat) > 3 else 0.0
            if fp[0] == 'module':                         # KiCad 5: pad angle relative
                prot += rot
            centre = np.array(org) + shp.rot(pxy, rot)
            drill = sx.item(pad, 'drill')
            off = sx.xy(drill, 'offset') if drill else None            # the copper's offset from the hole
            local = shp.pad_shape(pad)
            if off and any(off):
                local = affinity.translate(local, *off)
            geom = shp.place(local, centre, prot)
            lys = self._expand(sx.layers(pad))
            net = self._net_of(pad)
            sz = sx.xy(pad, 'size') or (0.0, 0.0)
            hole = [float(x) for x in drill[1:] if isinstance(x, (int, float)) or str(x).replace('.', '', 1).isdigit()] if drill else []
            if pad[2] == 'np_thru_hole' and hole and max(sz) <= max(hole) + 1e-9:
                lys = []                                            # a hole, no copper (it had been copper of no
            ends = [l for l in lys if l in ('F.Cu', 'B.Cu')]
            for l in lys:                                           # net on every layer)
                e = self._stack_entry(pad, l)
                g_l = geom
                if e is not None:                                   # KiCad 9+: its own shape on this layer
                    shape = sx.val(e, 'shape', pad[3] if len(pad) > 3 else 'rect')
                    local_l = shp.pad_shape(['pad', pad[1], pad[2], shape] + list(e[2:]) + list(pad[4:]))
                    if off and any(off):
                        local_l = affinity.translate(local_l, *off)
                    g_l = shp.place(local_l, centre, prot)
                self.items.append((l, net, g_l))
                self._unused(len(self.items) - 1, pad, ends)
            if pad[2] == 'thru_hole' and drill and len(lys) > 1:
                if len(drill) > 3 and drill[1] == 'oval':              # a slot: the barrel of its perimeter (its
                    a, b = sorted((float(drill[2]), float(drill[3])))  # long side as the diameter had overstated
                    d = (2 * (b - a) + math.pi * a) / math.pi          # it)
                else:
                    d = float(drill[1]) if drill[1] != 'oval' else float(drill[2])
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
                cu = self._expand(lys)
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
