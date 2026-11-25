"""
Lumped R / L / C parts from two-pad footprints: an element across the gap
between the pads, in their copper plane (units mm, y up, like the Board).

  kind / value  KiCad simulation fields first (Sim.Device R / L / C, Sim.Params
                "r=10k"), else the reference prefix R / L / C and the Value
                field ('4p7', '10k', '1.2nH', '0R5', ...)
  parasitics    optional footprint fields sim.esr / sim.esl make the part a
                series R-L-C
  skipped       DNP and exclude_from_sim footprints, Sim.Enable = 0, port
                footprints, parts whose two pads are not on modelled copper (net
                not in --nets) or not in line along x / y
  0 ohm         a metal strap between the pads instead of a lumped element
  package       the chip size from the footprint's name (R_0402_1005Metric:
                0402), its body L x W x H in mm (SIZES), centred between the
                pads: what the GUI's viewer draws; without a size in the name,
                the pads' outer extent
"""
import re
import numpy as np

from .units import parse_value, part_value

__all__ = ['kind_value', 'not_fitted']

# chip sizes (imperial code): body length, width, height in mm (typical EIA
# values)
SIZES = {
    '01005': (0.4, 0.2, 0.13), '0201': (0.6, 0.3, 0.26),
    '0402': (1.0, 0.5, 0.35), '0603': (1.6, 0.8, 0.45),
    '0805': (2.0, 1.25, 0.5), '1206': (3.2, 1.6, 0.55),
    '1210': (3.2, 2.5, 0.55), '1806': (4.5, 1.6, 0.6), '1812': (4.5, 3.2, 0.6),
    '2010': (5.0, 2.5, 0.6), '2512': (6.3, 3.2, 0.6)
}
METRIC = {
    '0402': '01005', '0603': '0201', '1005': '0402', '1608': '0603',
    '2012': '0805', '3216': '1206', '3225': '1210', '4516': '1806',
    '4532': '1812', '5025': '2010', '6332': '2512'
}


def package(lib):
    """The imperial chip size of a footprint name:
    'Resistor_SMD:R_0402_1005Metric' -> '0402', 'C_1608Metric' -> '0603',
    'L_0603' -> '0603'; None when the name has none."""
    name = str(lib or '').split(':')[-1]
    m = re.search(r'(?<!\d)(\d{4,5})_(\d{4})Metric', name)
    if m and m.group(1) in SIZES:
        return m.group(1)
    m = re.search(r'(?<!\d)(\d{4})Metric', name)
    if m and m.group(1) in METRIC:
        return METRIC[m.group(1)]
    m = re.search(
        r'(?<![\d])(01005|0201|0402|0603|0805|1206|1210|1806|1812|2010|2512)'
        r'(?![\d])',
        name)
    return m.group(1) if m else None


def _field(props, key):
    for k, v in props.items():
        if k.lower() == key.lower() and v not in (None, ''):
            return v
    return None


class Part:
    def __init__(self, ref, R, L, C, layer, z, axis, s, e):
        self.ref, self.R, self.L, self.C, self.layer, self.z, self.axis = (
            ref, R, L, C, layer, z, axis)
        self.s, self.e = np.asarray(s, float), np.asarray(e, float)
        given = [v for v in (R, L, C) if v is not None]
        # 0 ohm / 0 H link: metal
        self.short = given == [0.0] and C is None
        # R + L + C in series (ESR / ESL fields)
        self.series = len(given) > 1
        # the chip as drawn: size, (L, W, H), (x, y)
        self.package, self.body, self.centre = None, None, None

    @property
    def kind(self):
        """What the part is mainly: 'C' when it has a capacitance, else 'L' with
        an inductance, else 'R'."""
        return ('C' if self.C is not None else
                ('L' if self.L is not None else 'R'))

    def to_dict(self):
        d = dict(ref=self.ref, R=self.R, L=self.L, C=self.C, layer=self.layer,
                 z=self.z, axis=self.axis, s=[float(v) for v in self.s],
                 e=[float(v) for v in self.e])
        if self.body is not None:
            d.update(package=self.package, body=[float(v) for v in self.body],
                     centre=[float(v) for v in self.centre])
        return d

    @classmethod
    def from_dict(cls, d):
        p = cls(d['ref'], d['R'], d['L'], d['C'], d['layer'], d['z'], d['axis'],
                d['s'], d['e'])
        p.package, p.body, p.centre = (d.get('package'), d.get('body'),
                                       d.get('centre'))
        return p

    @property
    def gap(self):
        return float(self.e[self.axis] - self.s[self.axis])

    def box(self):
        return self.s, self.e

    def pieces(self):
        """openEMS lumped elements along the gap: [(suffix, {'R'|'L'|'C':
        value}, series topology, start, stop)]. openEMS's series-topology update
        (0.37) has an undamped DC mode unless a C is in the branch: series R + L
        grew by ~5 dB per 9000 steps, a lone series L drifted slowly upwards.
        So:
        a resistance in series with L / C becomes its own resistive sheet on the
        first half of the gap; an inductor without C uses the parallel topology
        (a plain inductor, plus the small parallel capacitance openEMS adds:
        self-resonance ~4.5 x fmax); L with C uses the series topology."""
        vals = {
            k: v
            for k, v in (('R', self.R), ('L', self.L), ('C', self.C))
            if v is not None
        }
        x = {k: v for k, v in vals.items() if k != 'R'}
        if not (self.series and 'R' in vals and x):
            return [('', vals, 'L' in vals and 'C' in vals, self.s, self.e)]
        a = self.axis
        mid_s, mid_e = self.s.copy(), self.e.copy()
        mid_s[a] = mid_e[a] = 0.5 * (self.s[a] + self.e[a])
        out = ([('_R', {'R': vals['R']}, False, self.s,
                 mid_e)] if vals['R'] > 0 else [])
        return (
            out
            + [('_X' if out else '', x, 'L' in x and 'C' in x, mid_s, self.e)])

    def value_str(self):
        if self.short:
            return "0 ohm (metal strap)"
        out = []
        for v, unit, scale in ((self.R, 'ohm', [
            (1e6, 'M'), (1e3, 'k'), (1, '')
        ]), (self.L, 'H', [(1e-6, 'u'), (1e-9, 'n'),
                           (1e-12, 'p')]), (self.C, 'F', [(1e-6, 'u'),
                                                          (1e-9, 'n'),
                                                          (1e-12, 'p'),
                                                          (1e-15, 'f')])):
            if v is None:
                continue
            f, p = next(((f, p) for f, p in scale if abs(v) >= f), scale[-1])
            out.append(f"{v / f:g} {p}{unit}")
        return (" + " if self.series else " || ").join(out)

    def describe(self):
        return (
            f"  {self.ref}: lumped {self.value_str()}, {self.layer}, gap "
            f"{self.gap:.3f} mm, width "
            f"{self.e[1 - self.axis] - self.s[1 - self.axis]:.3f} mm")


def kind_value(fp):
    """('R'|'L'|'C', value) of a footprint, or (None, reason)."""
    props = fp['props']
    dev = (_field(props, 'Sim.Device') or '').strip().upper()
    if dev in ('R', 'L', 'C'):
        params = _field(props, 'Sim.Params') or ''
        mt = re.search(rf'\b{dev.lower()}\s*=\s*"?([^"\s]+)', params, re.I)
        v = parse_value(mt.group(1) if mt else fp['value'])
        return ((dev, v) if v is not None else
                (None, f"value '{fp['value']}' not understood"))
    if dev:
        return None, None  # some other simulation model
    words = re.split(r'[_\s]+', str(fp['value']).strip())
    if _nf_value(fp):  # (DNP and excluded parts: find_parts says so)
        return None, f"not fitted ('{fp['value']}')"
    if re.match(r'^FB\d', fp['ref']):
        # a ferrite bead: its rated impedance as a resistance (it blocks at RF:
        # 'FB_600R_0805_MPZ2012S601AT000', '600_Ohm_@_100MHz')
        for w in words:
            mo = re.fullmatch(r'(\d+(?:\.\d+)?)(R|ohms?)', w, re.I)
            if mo:
                return 'R', float(mo.group(1))
        return None, f"ferrite '{fp['value']}' without its impedance"
    mt = re.match(r'^([RLC])\d', fp['ref'])
    if not mt:
        return None, None
    # the value first, then the package and part number:
    # '1000pF_0402_GRM1555C1H102GA01D', '0R_0402_RC0402JR-070RL' (a LimeRFE's);
    # or a library's 'C_22u_16V_0603', 'R_45k3_0402', 'R_0603_10k': the kind's
    # letter, then the value (all 788 R / L / C of the Jetson board), a package
    # code passed over (it had been read as 603 ohm)
    v = part_value(fp['value'], mt.group(1))
    return ((mt.group(1), v) if v is not None else
            (None, f"value '{fp['value']}' not understood"))


def not_fitted(fp):
    """Whether a footprint is not fitted: DNP or excluded from simulation, or
    its value says so ('NF', '0RNF_0402_Yageo', 'NF_SMD_0603', 'DNP', 'DNF':
    a LimeRFE's BOM marks R90, R93, R374, R375 'NF', not assembled). Case
    matters ('1nF' is a capacitance)."""
    return bool(fp.get('dnp') or fp.get('exclude_from_sim') or _nf_value(fp))


def _nf_value(fp):
    """Whether a footprint's value marks it not fitted."""
    w = re.split(r'[_\s]+', str(fp.get('value', '')).strip())[0]
    return bool(re.fullmatch(r'(0R)?NF|DNP|DNF|NP|NC', w))


def _copper_gap(board, layer, axis, e1, e2, cb, w, nets=None):
    """The copper-free part of the gap between a part's pads (e1, e2 along axis,
    the part's width w about cb): tracks drawn to a pad's centre reach into the
    gap with their copper, so the element spans from where the copper of each
    side ends. An element overlapping copper is shorter than drawn in openEMS
    (its metal wins): a 100 ohm resistor fed from the side, the lines' copper
    0.27 mm into a 1 mm gap, measured 67 ohm, and the Wilkinson divider's
    isolation came out -18.5 dB instead of about -39."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    band = [0.0] * 4
    band[axis], band[2 + axis] = e1, e2
    band[1 - axis], band[3 - axis] = cb - w / 2, cb + w / 2
    band = box(*band)
    # the layer's copper that meets the band, through a spatial index made once
    # a board and layer (each part's gap had unioned the whole layer: 0.61 s of
    # a 0.95 s board build with 44 parts)
    cache = board.__dict__.setdefault('_copper_index', {})
    polys = board.copper.get(layer, [])
    if cache.get(layer, (None, None))[0] is not polys:
        cache[layer] = (polys, STRtree(polys) if polys else None)
    tree = cache[layer][1]
    idx = ([] if tree is None else
           list(tree.query(band, predicate='intersects')))
    if nets is not None:
        # each end where its own pad's net's copper ends; another net's copper
        # in the gap (a pour's fill between the pads) is no bridge, nor one
        # pad's copper alone across the band (a part at 45 degrees: a
        # LimeRFE's DC blocks and filter capacitors had been skipped as
        # bridged): bridged where the two nets' copper meets
        cn = board.copper_nets.get(layer, [])
        lo, hi = e1, e2
        for i in idx:
            n = cn[i] if i < len(cn) else None
            g = polys[i].intersection(band)
            for piece in getattr(g, 'geoms', [g]):
                if piece.is_empty or piece.area < 1e-9:
                    continue
                a0, a1 = piece.bounds[axis], piece.bounds[2 + axis]
                if n == nets[0] and a0 <= e1 + 1e-6:
                    lo = max(lo, a1)
                elif n == nets[1] and a1 >= e2 - 1e-6:
                    hi = min(hi, a0)
        return (lo, hi) if hi > lo else (e1, e1)
    near = [polys[i] for i in idx]
    cu = unary_union([g.intersection(band) for g in near])
    lo, hi = e1, e2
    for piece in getattr(cu, 'geoms', [cu]):
        if piece.is_empty or piece.area < 1e-9:
            continue
        a0, a1 = piece.bounds[axis], piece.bounds[2 + axis]
        if a0 <= e1 + 1e-6 and a1 >= e2 - 1e-6:
            return e1, e1                                       # bridged
        if a0 <= e1 + 1e-6:
            lo = max(lo, a1)
        elif a1 >= e2 - 1e-6:
            hi = min(hi, a0)
    return lo, hi


def _chord(pad, centre, u):
    """The length of the line through `centre` along unit vector u inside the
    pad's polygon."""
    from shapely.geometry import LineString
    r = 4 * max(pad.bounds[2] - pad.bounds[0], pad.bounds[3] - pad.bounds[1])
    seg = LineString([tuple(centre - r * u), tuple(centre + r * u)])
    return float(pad.intersection(seg).length)


def _strap(board, layer, axis, a0, cb, w, pad, centre, back=False):
    """A copper strap on `layer` from an end of an angled part's element (at
    a0 along axis, its width w about cb across) to a pad (pad, its centre): a
    rectangle w long along axis from a0 (back: towards lower a, the near end's)
    and across from the element to the pad's centre, joined to the copper the
    pad sits on. False where the pad is on no modelled copper."""
    from shapely.geometry import Point, box
    from shapely.geometry.polygon import orient
    b = 1 - axis
    polys = board.copper.get(layer, [])
    at = Point(*centre)
    k = next((i for i, q in enumerate(polys) if q.distance(at) < 1e-6), None)
    if k is None:
        return False
    span = min(w, pad.bounds[2 + axis] - pad.bounds[axis])
    r = [0.0] * 4
    r[axis], r[2 + axis] = (a0 - span, a0) if back else (a0, a0 + span)
    r[b] = min(cb - w / 2, float(centre[b]))
    r[2 + b] = max(cb + w / 2, float(centre[b]))
    joined = polys[k].union(box(*r))
    if joined.geom_type != 'Polygon':
        return False
    new = list(polys)  # (a new list: the copper index rebuilds)
    new[k] = orient(joined, 1.0)
    board.copper[layer] = new
    return True


def find_parts(board):
    """Lumped parts among the footprints of a Board (board.Board: its
    primitives, model coordinates) that sit on its copper. Returns (parts,
    notes)."""
    parts, notes = [], []
    inside = board.outline.buffer(1e-6)
    for fp in board.prims.footprints:
        if fp['ref'] in board.port_refs:
            continue
        if not any(inside.intersects(p['geom']) for p in fp['pads']):
            continue  # outside the board / region: not a note
        kind, val = kind_value(fp)
        if kind is None:
            if val:
                notes.append(f"part {fp['ref']} skipped: {val}")
            continue
        ref = fp['ref']
        if (fp.get('dnp') or fp.get('exclude_from_sim') or str(
                _field(fp['props'], 'Sim.Enable') or '1') in ('0', 'false')):
            notes.append(f"part {ref} skipped: DNP / excluded from simulation")
            continue
        pads = [p for p in fp['pads'] if p['layers']]
        if len(pads) != 2:
            notes.append(
                f"part {ref} skipped: {len(pads)} copper pads (lumped parts "
                "need 2)"
            )
            continue
        lays = [
            l for l in pads[0]['layers']
            if l in pads[1]['layers'] and l in board.cu_z
        ]
        side = 'B.Cu' if fp['side'] == 'B' else 'F.Cu'
        lays.sort(key=lambda l: l != side)
        g = [p['geom'] for p in pads]
        lay = next((l for l in lays if all(
            board.copper_at(l, q.centroid) is not None for q in g)), None)
        if lay is None:
            notes.append(
                f"part {ref} skipped: its pads are not on modelled copper (add "
                "their nets to --nets)"
            )
            continue
        c = [np.array(q.centroid.coords[0]) for q in g]
        d = c[1] - c[0]
        axis = int(np.argmax(np.abs(d)))
        lo, hi = (0, 1) if d[axis] > 0 else (1, 0)
        b = 1 - axis
        angled = abs(d[b]) > 0.01
        if not angled:
            e1, e2 = g[lo].bounds[2 + axis], g[hi].bounds[axis]  # inner edges
            w = min(q.bounds[2 + b] - q.bounds[b] for q in g)
            cb = 0.5 * (c[0][b] + c[1][b])
        else:
            # at an angle (R-E5): each pad's inner edge where the line between
            # their centres leaves it (a rotated pad's box overlaps the
            # other's), the element along the dominant axis between them on the
            # first pad's side, and a copper strap across to the second pad (in
            # the board's copper: every solver has it)
            u = (c[hi] - c[lo]) / np.linalg.norm(c[hi] - c[lo])
            n = np.array([-u[1], u[0]])
            p1 = c[lo] + u * _chord(g[lo], c[lo], u) / 2
            p2 = c[hi] - u * _chord(g[hi], c[hi], u) / 2
            e1, e2, cb = float(p1[axis]), float(p2[axis]), float(p1[b])
            w = 0.5 * min(_chord(q, cq, n) for q, cq in zip(g, c))
        if e2 - e1 <= 1e-6:
            notes.append(f"part {ref} skipped: pads overlap"
                         + (f" (rotation {fp['rot']:g} deg)" if angled else ""))
            continue
        if angled:
            from shapely.geometry import Point
            start = [0.0, 0.0]
            start[axis], start[b] = e1 - 1e-3, cb
            # the element's near end on its pad's copper too (the pad's edge is
            # at an angle: the end may stand just off it)
            on = board.copper_at(lay, Point(*start)) is not None
            if not (on or _strap(board, lay, axis, e1, cb, w, g[lo], c[lo],
                                 back=True)) \
                    or not _strap(board, lay, axis, e2, cb, w, g[hi], c[hi]):
                notes.append(
                    f"part {ref} skipped: at {fp['rot']:g} deg, no copper of "
                    "its second pad to strap to")
                continue
            notes.append(
                f"part {ref} at {fp['rot']:g} deg: its element along "
                f"{'xy'[axis]} from pad {pads[lo]['name']}, a copper strap "
                f"along {'xy'[b]} to pad {pads[hi]['name']}")
        if not angled:
            # (an angled part's pads overlap along its element's axis: its
            # element and strap come from the pads' geometry above; trimmed
            # here, a LimeRFE's 45-degree DC blocks read as bridged)
            e1, e2 = _copper_gap(board, lay, axis, e1, e2, cb, w,
                                 (pads[lo].get('net'), pads[hi].get('net')))
        if e2 - e1 <= 1e-6:
            notes.append(
                f"part {ref} skipped: copper bridges the gap between its pads")
            continue
        z = board.cu_z[lay]
        s, e = np.zeros(3), np.zeros(3)
        s[axis], e[axis] = e1, e2
        s[b], e[b] = cb - w / 2, cb + w / 2
        s[2] = e[2] = z
        if kind == 'C' and val == 0:
            notes.append(f"part {ref} skipped: 0 F is an open circuit")
            continue
        R, L, C = (val if kind == k else None for k in 'RLC')
        for key, name in (('sim.esr', 'R'), ('sim.esl', 'L')):
            pv = _field(fp['props'], key)
            if pv is not None:
                v = parse_value(pv)
                if v is None:
                    notes.append(
                        f"part {ref}: {key} '{pv}' not understood, ignored")
                elif name == 'R':
                    R = v if R is None else R + v
                else:
                    L = v if L is None else L + v
        part = Part(ref, R, L, C, lay, z, axis, s, e)
        part.package = package(fp.get('lib'))
        if part.package:
            part.body = SIZES[part.package]
        else:  # the pads' outer extent, as tall as an 0402
            part.body = (max(q.bounds[2 + axis]
                             for q in g) - min(q.bounds[axis]
                                               for q in g), w, 0.35)
        mid = 0.5 * (c[0] + c[1])
        part.centre = (float(mid[0]), float(mid[1]))
        parts.append(part)
    return parts, notes
