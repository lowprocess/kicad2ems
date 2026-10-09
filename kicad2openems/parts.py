"""
Lumped R / L / C parts from two-pad footprints, modelled as openEMS lumped elements in the copper
plane between the pads (units mm, y up, like Model).

  kind / value   KiCad simulation fields first (Sim.Device R / L / C, Sim.Params "r=10k"), else the
                 reference prefix R / L / C and the Value field ('4p7', '10k', '1.2nH', '0R5', ...)
  parasitics     optional footprint fields sim.esr / sim.esl make the part a series R-L-C
  skipped        DNP and exclude_from_sim footprints, Sim.Enable = 0, port footprints, parts whose
                 two pads are not on modelled copper (net not in --nets) or not in line along x / y
  0 ohm          a metal strap between the pads instead of a lumped element
"""
import re
import numpy as np
from shapely.geometry import Point
from .geometry import flip_y

_PREFIX = {'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6, 'm': 1e-3, 'k': 1e3, 'K': 1e3,
           'M': 1e6, 'G': 1e9, 'R': 1.0, 'r': 1.0, 'E': 1.0, '': 1.0}
_VALUE = re.compile(r'^(\d*)(?:[.,](\d+))?([fpnuµμmkKMGRrE]?)(\d*)(F|H|Ω|ohms?|OHMS?)?$')


def parse_value(text):
    """'4p7' -> 4.7e-12, '10k' -> 1e4, '1.2nH' -> 1.2e-9, '0R5' -> 0.5, '100n' -> 1e-7, '1M' -> 1e6.
    Only the first word counts ('100n 50V X7R'); returns None if it is not a value."""
    t = re.sub(r'^([\d.,]+)\s+(?=[fpnuµμmkKMGRrFHΩo])', r'\1', str(text).strip())    # '2.2 nH'
    word = re.split(r'[\s/_]', t)[0].replace('Ω', '')
    mt = _VALUE.match(word) if word else None
    if not mt:
        return None
    ip, fp, pre, tail, _ = mt.groups()
    if not (ip or fp or tail) or (tail and fp):
        return None
    digits = f"{ip or '0'}.{fp or tail or '0'}"
    return float(digits) * _PREFIX[pre]


def _field(props, key):
    for k, v in props.items():
        if k.lower() == key.lower() and v not in (None, ''):
            return v
    return None


class Part:
    def __init__(self, ref, R, L, C, layer, z, axis, s, e):
        self.ref, self.R, self.L, self.C, self.layer, self.z, self.axis = ref, R, L, C, layer, z, axis
        self.s, self.e = np.asarray(s, float), np.asarray(e, float)
        given = [v for v in (R, L, C) if v is not None]
        self.short = given == [0.0] and C is None                   # 0 ohm / 0 H link: metal
        # one L alone, or several elements: series (openEMS default topology is parallel)
        self.series = len(given) > 1 or (L is not None and len(given) == 1)

    @property
    def gap(self):
        return float(self.e[self.axis] - self.s[self.axis])

    def box(self):
        return self.s, self.e

    def value_str(self):
        if self.short:
            return "0 ohm (metal strap)"
        out = []
        for v, unit, scale in ((self.R, 'ohm', [(1e6, 'M'), (1e3, 'k'), (1, '')]),
                               (self.L, 'H', [(1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p')]),
                               (self.C, 'F', [(1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p'), (1e-15, 'f')])):
            if v is None:
                continue
            f, p = next(((f, p) for f, p in scale if abs(v) >= f), scale[-1])
            out.append(f"{v / f:g} {p}{unit}")
        return (" + " if self.series else " || ").join(out)

    def describe(self):
        return (f"  {self.ref}: lumped {self.value_str()}, {self.layer}, gap {self.gap:.3f} mm, "
                f"width {self.e[1 - self.axis] - self.s[1 - self.axis]:.3f} mm")


def _kind_value(fp):
    """('R'|'L'|'C', value) of a footprint, or (None, reason)."""
    props = fp['props']
    dev = (_field(props, 'Sim.Device') or '').strip().upper()
    if dev in ('R', 'L', 'C'):
        params = _field(props, 'Sim.Params') or ''
        mt = re.search(rf'\b{dev.lower()}\s*=\s*"?([^"\s]+)', params, re.I)
        v = parse_value(mt.group(1) if mt else fp['value'])
        return (dev, v) if v is not None else (None, f"value '{fp['value']}' not understood")
    if dev:
        return None, None                                           # some other simulation model
    mt = re.match(r'^([RLC])\d', fp['ref'])
    if not mt:
        return None, None
    v = parse_value(fp['value'])
    return (mt.group(1), v) if v is not None else (None, f"value '{fp['value']}' not understood")


def find_parts(board, model):
    """Lumped parts of the board that sit on the model's copper. Returns (parts, notes)."""
    parts, notes = [], []
    port_refs = set()
    for v in model.port_fps.values():
        for fp in (v.values() if isinstance(v, dict) and 'props' not in v else [v]):
            port_refs.add(fp['ref'])
    for fp in board.footprints:
        if fp['ref'] in port_refs:
            continue
        kind, val = _kind_value(fp)
        if kind is None:
            if val:
                notes.append(f"part {fp['ref']} skipped: {val}")
            continue
        ref = fp['ref']
        if fp.get('dnp') or fp.get('exclude_from_sim') or str(_field(fp['props'], 'Sim.Enable') or '1') in ('0', 'false'):
            notes.append(f"part {ref} skipped: DNP / excluded from simulation")
            continue
        pads = [p for p in fp['pads'] if p['layers']]
        if len(pads) != 2:
            notes.append(f"part {ref} skipped: {len(pads)} copper pads (lumped parts need 2)")
            continue
        lays = [l for l in pads[0]['layers'] if l in pads[1]['layers'] and l in model.cu_z]
        side = 'B.Cu' if fp['side'] == 'B' else 'F.Cu'
        lays.sort(key=lambda l: l != side)
        g = [flip_y(p['geom']) for p in pads]
        lay = next((l for l in lays if all(model._copper_at(l, q.centroid) is not None for q in g)), None)
        if lay is None:
            notes.append(f"part {ref} skipped: its pads are not on modelled copper (add their nets to --nets)")
            continue
        c = [np.array(q.centroid.coords[0]) for q in g]
        d = c[1] - c[0]
        axis = int(np.argmax(np.abs(d)))
        if abs(d[1 - axis]) > 0.01:
            notes.append(f"part {ref} skipped: pads not in line along x or y (rotation {fp['rot']:g} deg)")
            continue
        lo, hi = (0, 1) if d[axis] > 0 else (1, 0)
        e1, e2 = g[lo].bounds[2 + axis], g[hi].bounds[axis]          # inner pad edges
        if e2 - e1 <= 1e-6:
            notes.append(f"part {ref} skipped: pads overlap")
            continue
        b = 1 - axis
        w = min(q.bounds[2 + b] - q.bounds[b] for q in g)
        cb = 0.5 * (c[0][b] + c[1][b])
        z = model.cu_z[lay]
        s, e = np.zeros(3), np.zeros(3)
        s[axis], e[axis] = e1, e2
        s[b], e[b] = cb - w / 2, cb + w / 2
        s[2] = e[2] = z
        if kind == 'C' and val == 0:
            notes.append(f"part {ref} skipped: 0 F is an open circuit")
            continue
        R = L = C = None
        if kind == 'R':
            R = val
        elif kind == 'L':
            L = val
        else:
            C = val
        for key, name in (('sim.esr', 'R'), ('sim.esl', 'L')):
            pv = _field(fp['props'], key)
            if pv is not None:
                v = parse_value(pv)
                if v is None:
                    notes.append(f"part {ref}: {key} '{pv}' not understood, ignored")
                elif name == 'R':
                    R = v if R is None else R + v
                else:
                    L = v if L is None else L + v
        parts.append(Part(ref, R, L, C, lay, z, axis, s, e))
    return parts, notes
