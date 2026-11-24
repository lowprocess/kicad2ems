"""ODB++ (a job directory, .tgz/.tar or .zip) -> Board, with nets.

Read: matrix/matrix (layer order, types, polarity, drill spans), the step's
copper layers (features: lines, pads, arcs, surfaces with every standard
symbol, thermals with their spokes; negative features clear), NEGATIVE plane
layers (the profile filled, features clearing it), drill layers (vias),
eda/data (which net each feature belongs to), the stack-up from
matrix/stackup.xml or else the layers' attributes, and the profile
(outline). ODB++ has y up; the Board gets y down like KiCad.
"""
import gzip
import io
import math
import os
import re
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import contextmanager

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from ..model import Board, Layer, Via
from . import lzw, shapes as shp

COPPER_TYPES = {'SIGNAL', 'POWER_GROUND', 'MIXED'}


# ---- files ----------------------------------------------------------------
@contextmanager
def _job_root(path):
    """The job directory (the one holding matrix/matrix), extracting an
    archive to a temporary directory first."""
    path = str(path)
    tmp = None
    if os.path.isfile(path):
        tmp = tempfile.TemporaryDirectory(prefix='odbpp_')
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                z.extractall(tmp.name)                 # zipfile strips '..' parts
        else:
            with tarfile.open(path) as t:
                t.extractall(tmp.name, filter='data')   # no paths outside tmp
        path = tmp.name
    try:
        for d, dirs, _ in os.walk(path):
            m = os.path.join(d, 'matrix')
            if os.path.isdir(m) and any(f.lower().startswith('matrix') for f in os.listdir(m)):
                yield d
                return
            if d.count(os.sep) - path.count(os.sep) >= 2:
                dirs.clear()                        # the job sits at most two levels down
        raise FileNotFoundError(f'{path}: no ODB++ job (matrix/matrix) found')
    finally:
        if tmp:
            tmp.cleanup()


def _text(path):
    """A file's text; also its compressed forms (.Z, .z, .gz)."""
    for ext, fn in (('', lambda b: b), ('.Z', lzw.decompress), ('.z', lzw.decompress),
                    ('.gz', gzip.decompress)):
        if os.path.isfile(path + ext):
            with open(path + ext, 'rb') as fh:
                return fn(fh.read()).decode('latin-1')
    return None


def _ci(dirpath, name):
    """`name` inside `dirpath`, matched case-insensitively."""
    if not os.path.isdir(dirpath):
        return None
    for n in os.listdir(dirpath):
        if n.lower() == name.lower():
            return os.path.join(dirpath, n)
    return None


def _keyvals(text):
    """KEY=VALUE lines -> dict (keys upper-cased, as ODB++ is case-blind)."""
    out = {}
    for line in (text or '').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            out[k.strip().upper()] = v.strip()
    return out


def _matrix(text):
    """matrix/matrix -> (steps [name], layers [dict]) - its STEP and LAYER
    blocks of KEY=VALUE lines."""
    steps, layers = [], []
    for kind, body in re.findall(r'(STEP|LAYER)\s*\{(.*?)\}', text, re.S):
        kv = _keyvals(body)
        (steps if kind == 'STEP' else layers).append(kv)
    layers.sort(key=lambda l: int(l.get('ROW', 0)))
    return [s.get('NAME', '') for s in steps], layers


# ---- feature files ----------------------------------------------------------
_UNITS = {'INCH': (25.4, 0.0254), 'MM': (1.0, 0.001)}   # coordinates, symbol sizes


def _ring(outer, inner):
    return outer.difference(inner)


def _thermal(ring, angle, spokes, gap):
    """A thermal relief: `ring` with `spokes` straight slots of width `gap`
    cut at angle + k 360/spokes (degrees, counter-clockwise from +x)."""
    reach = 2 * max(abs(c) for c in ring.bounds) + 1.0
    for k in range(max(int(spokes), 0)):
        slot = affinity.rotate(box(0, -gap / 2, reach, gap / 2),
                               angle + k * 360.0 / spokes, origin=(0, 0))
        ring = ring.difference(slot)
    return ring


def _rect(w, h, rad=0.0, cham=0.0):
    """A w x h rectangle, corners rounded by `rad` or cut by `cham`."""
    if rad > 0:
        r = min(rad, min(w, h) / 2 - 1e-9)
        return box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, quad_segs=4)
    if cham > 0:
        c = min(cham, min(w, h) / 2)
        return Polygon([(-w / 2 + c, -h / 2), (w / 2 - c, -h / 2), (w / 2, -h / 2 + c),
                        (w / 2, h / 2 - c), (w / 2 - c, h / 2), (-w / 2 + c, h / 2),
                        (-w / 2, h / 2 - c), (-w / 2, -h / 2 + c)])
    return box(-w / 2, -h / 2, w / 2, h / 2)


def _standard_symbol(name, size_unit):
    """A standard ODB++ symbol (spec section "Standard Symbols") as geometry
    at the origin (y up), or None if `name` is not one. Sizes are scaled by
    `size_unit` (mils or microns to mm); angles and spoke counts are not.
    Thermals are the copper ring between their outer and inner size with
    the spoke slots cut out (rounded line ends are drawn square)."""
    m = re.fullmatch(r'([a-z_]+)([\d.].*)?', name.lower())
    if not m:
        return None
    kind, args = m.group(1), (m.group(2) or '').split('x')
    v, tag = [], {}
    for a in args:                              # positional numbers, then xr<rad> / xc<cut>
        if re.fullmatch(r'[\d.]+', a) and not tag:
            v.append(float(a))
        elif re.fullmatch(r'[rc][\d.]+', a):
            tag[a[0]] = float(a[1:]) * size_unit
    L = lambda i: v[i] * size_unit             # a length parameter
    circ = lambda d: Point(0, 0).buffer(d / 2, quad_segs=16)
    try:
        if kind == 'null':
            return Polygon()
        if kind in ('r', 'hole', 'bfr') and v:
            g = circ(L(0))
            if kind == 'bfr':                   # butterfly: the 1st and 3rd quadrants
                d = L(0)
                g = g.intersection(box(0, 0, d, d).union(box(-d, -d, 0, 0)))
            return g
        if kind in ('s', 'bfs') and v:
            g = _rect(L(0), L(0))
            if kind == 'bfs':
                d = L(0)
                g = g.intersection(box(0, 0, d, d).union(box(-d, -d, 0, 0)))
            return g
        if kind == 'rect':
            return _rect(L(0), L(1), tag.get('r', 0.0), tag.get('c', 0.0))
        if kind in ('oval', 'oval_h'):
            return shp.pad_shape('oval', L(0), L(1))
        if kind == 'di':
            return shp.pad_shape('diamond', L(0), L(1))
        if kind == 'oct':                       # corner: the cut along each side
            return _rect(L(0), L(1), cham=L(2) if len(v) > 2 else min(L(0), L(1)) * 0.29)
        if kind == 'el':
            return affinity.scale(circ(1.0), L(0), L(1))
        if kind == 'tri':
            return Polygon([(-L(0) / 2, -L(1) / 2), (L(0) / 2, -L(1) / 2), (0, L(1) / 2)])
        if kind in ('hex_l', 'hex_s'):
            w, h, r = L(0), L(1), L(2)
            pts = [(-w / 2, 0), (-w / 2 + r, h / 2), (w / 2 - r, h / 2), (w / 2, 0),
                   (w / 2 - r, -h / 2), (-w / 2 + r, -h / 2)]
            if kind == 'hex_s':
                pts = [(-w / 2, -h / 2 + r), (-w / 2, h / 2 - r), (0, h / 2), (w / 2, h / 2 - r),
                       (w / 2, -h / 2 + r), (0, -h / 2)]
            return Polygon(pts)
        if kind == 'donut_r':
            return _ring(circ(L(0)), circ(L(1)))
        if kind == 'donut_s':
            r = tag.get('r', 0.0)
            return _ring(_rect(L(0), L(0), r), _rect(L(1), L(1), max(r - (L(0) - L(1)) / 2, 0)))
        if kind == 'donut_sr':
            return _ring(_rect(L(0), L(0)), circ(L(1)))
        if kind == 'donut_rc':
            w, h, lw = L(0), L(1), L(2)
            return _ring(_rect(w, h, tag.get('r', 0.0)), _rect(w - 2 * lw, h - 2 * lw))
        if kind == 'donut_o':
            w, h, lw = L(0), L(1), L(2)
            return _ring(shp.pad_shape('oval', w, h), shp.pad_shape('oval', w - 2 * lw, h - 2 * lw))
        # thermals: (outer, inner | ring width) x angle x spokes x gap
        if kind in ('thr', 'ths'):
            return _thermal(_ring(circ(L(0)), circ(L(1))), v[2], v[3], L(4))
        if kind in ('s_ths', 's_tho', 's_thr'):
            ring = _ring(_rect(L(0), L(0), tag.get('r', 0.0)), _rect(L(1), L(1)))
            return _thermal(ring, 45.0 if kind == 's_thr' else v[2],
                            4 if kind == 's_thr' else v[3], L(4))
        if kind == 'sr_ths':
            return _thermal(_ring(_rect(L(0), L(0)), circ(L(1))), v[2], v[3], L(4))
        if kind in ('rc_ths', 'rc_tho'):
            w, h, lw = L(0), L(1), L(5)
            ring = _ring(_rect(w, h, tag.get('r', 0.0)), _rect(w - 2 * lw, h - 2 * lw))
            return _thermal(ring, v[2], v[3], L(4))
        if kind in ('o_ths', 'oblong_ths'):
            w, h, lw = L(0), L(1), L(5)
            ring = _ring(shp.pad_shape('oval', w, h), shp.pad_shape('oval', w - 2 * lw, h - 2 * lw))
            return _thermal(ring, v[2], v[3], L(4))
    except (IndexError, ValueError):
        return None
    return None


class _Features:
    """Parses feature files into [(index, geometry mm (y up), positive)].
    Standard symbol sizes are mils (inch files) or microns (mm files);
    user-defined symbols are read from the job's symbols/ directory."""

    def __init__(self, job):
        self.job, self.cache = job, {}

    def symbol(self, name, size_unit):
        key = (name, size_unit)
        if key not in self.cache:
            g = _standard_symbol(name, size_unit)
            if g is None:                       # a user-defined symbol
                d = _ci(os.path.join(self.job, 'symbols'), name)
                text = _text(os.path.join(d, 'features')) if d else None
                if text is not None:
                    g = shapely.Polygon()
                    for _, geom, pos in self.parse(text):
                        g = g.union(geom) if pos else g.difference(geom)
            self.cache[key] = g
        return self.cache[key]

    def parse(self, text):
        unit, size_unit = _UNITS['INCH']
        syms, out, idx = {}, [], -1
        surf, contour, cur = None, None, None
        for raw in text.splitlines():
            line = raw.split(';')[0].strip()
            if not line or line[0] == '#':
                continue
            tok = line.split()
            head = tok[0]
            if head.startswith('UNITS=') or (head == 'U' and len(tok) > 1):
                u = head[6:] if head.startswith('UNITS=') else tok[1]
                unit, size_unit = _UNITS.get(u.upper(), _UNITS['INCH'])
            elif head[0] == '$' and len(tok) > 1:
                su = {'I': 0.0254, 'M': 0.001}.get(tok[2].upper(), size_unit) if len(tok) > 2 else None
                syms[int(head[1:])] = (tok[1], su)
            elif surf is not None:              # inside a surface record
                if head == 'OB':
                    contour = [(float(tok[1]) * unit, float(tok[2]) * unit)]
                    cur = tok[3] if len(tok) > 3 else 'I'
                elif head == 'OS':
                    contour.append((float(tok[1]) * unit, float(tok[2]) * unit))
                elif head == 'OC':
                    end = (float(tok[1]) * unit, float(tok[2]) * unit)
                    ctr = (float(tok[3]) * unit, float(tok[4]) * unit)
                    contour.extend(map(tuple, shp.arc_points(contour[-1], end, ctr,
                                                             tok[5].upper() == 'Y')[1:]))
                elif head == 'OE':
                    if len(contour) >= 3:
                        p = shp.clean(Polygon(contour))
                        surf[0] = surf[0].union(p) if cur == 'I' else surf[0].difference(p)
                elif head == 'SE':
                    out.append((idx, surf[0], surf[1]))
                    surf = None
            elif head in ('L', 'P', 'A', 'T', 'B', 'S'):
                idx += 1                        # every feature record is numbered
                g, pos = self._feature(head, tok, syms, unit, size_unit)
                if head == 'S':
                    surf = [shapely.Polygon(), tok[1] == 'P']
                elif g is not None:
                    out.append((idx, g, pos))
        return out

    def _feature(self, head, tok, syms, unit, size_unit):
        """One L / P / A record -> (geometry, positive); T and B (text,
        barcodes) carry no copper we need."""
        f = lambda i: float(tok[i]) * unit
        if head == 'L':
            name, su = syms.get(int(tok[5]), ('r0', None))
            w = self._width(name, su or size_unit)
            return shp.stroke(np.array([(f(1), f(2)), (f(3), f(4))]), w,
                              square=name.startswith('s')), tok[6] == 'P'
        if head == 'A':
            name, su = syms.get(int(tok[7]), ('r0', None))
            pts = shp.arc_points((f(1), f(2)), (f(3), f(4)), (f(5), f(6)),
                                 tok[10].upper() == 'Y' if len(tok) > 10 else True)
            return shp.stroke(pts, self._width(name, su or size_unit)), tok[8] == 'P'
        if head == 'P':
            k, resize = 3, 1.0
            if tok[3] == '-1':                  # -1 <sym> <resize factor>
                k, resize = 4, float(tok[5]) / 1000.0
            name, su = syms.get(int(tok[k]), ('r0', None))
            g = self.symbol(name, su or size_unit)
            if g is None or g.is_empty:
                return None, True
            rest = tok[k + 1 + (1 if tok[3] == '-1' else 0):]
            pol = rest[0] if rest else 'P'
            orient = int(rest[2]) if len(rest) > 2 else 0
            if orient >= 8:                     # 8: any angle; 9: mirrored, any angle
                angle, mirror = float(rest[3]), orient == 9
            else:
                angle, mirror = 90.0 * (orient % 4), orient >= 4
            if resize != 1.0:
                g = affinity.scale(g, resize, resize, origin=(0, 0))
            if mirror:
                g = affinity.scale(g, -1, 1, origin=(0, 0))
            if angle:
                g = affinity.rotate(g, -angle, origin=(0, 0))   # ODB++ turns clockwise
            return affinity.translate(g, f(1), f(2)), pol == 'P'
        return None, True

    @staticmethod
    def _width(name, size_unit):
        n = re.findall(r'[\d.]+', name)
        return min(float(x) for x in n[:2]) * size_unit if n else 0.0


# ---- the board --------------------------------------------------------------
def _nets(text):
    """eda/data -> {(layer name, feature index): net name}."""
    out, layers, net = {}, [], ''
    for line in (text or '').splitlines():
        tok = line.split(';')[0].split()
        if not tok:
            continue
        if tok[0] == 'LYR':
            layers = [t.lower() for t in tok[1:]]
        elif tok[0] == 'NET':
            net = tok[1] if len(tok) > 1 and tok[1] != '$NONE$' else ''
        elif tok[0] == 'FID' and len(tok) >= 4 and net:
            li = int(tok[2])
            if li < len(layers):
                out[layers[li], int(tok[3])] = net
    return out


def _flip(g):
    """ODB++ (y up) to Board coordinates (y down)."""
    return affinity.scale(g, 1, -1, origin=(0, 0))


_XML_UNITS = {'MM': 1.0, 'MICRON': 0.001, 'INCH': 25.4, 'MIL': 0.0254}


def _stackup_xml(path, copper):
    """matrix/stackup.xml (ODB++ 8.1) -> [Layer] top to bottom, or None if it
    is missing or does not name every copper layer of the matrix (the matrix
    wins). A layer's material comes from SpecRef -> Spec -> Material; a
    length's units are the nearest Units attribute, then DefaultUnits."""
    if not path:
        return None
    root = ET.parse(path).getroot()
    tag = lambda e: e.tag.rsplit('}', 1)[-1]     # without any XML namespace
    default = _XML_UNITS.get(root.get('DefaultUnits', 'MIL').upper(), 0.0254)
    eda = next((e for e in root if tag(e) == 'EdaData'), None)
    if eda is None:
        return None
    mats = {}                                   # (spec, material) -> (thickness mm, er, tand, oz)
    for spec in (e for e in eda.iter() if tag(e) == 'Spec'):
        for mat in (e for e in spec if tag(e) == 'Material'):
            t, er, tand, oz = None, None, None, None
            for e in mat.iter():
                u = _XML_UNITS.get((e.get('Units_Thickness') or e.get('Units') or '').upper(), default)
                if tag(e) == 'Default_Thickness' and t is None:
                    t = float(e.get('Thickness', 0)) * u
                elif tag(e) == 'Property':
                    if e.get('Thickness'):
                        t = float(e.get('Thickness')) * u
                    if er is None and e.get('DielectricConstant_Dk'):
                        er = float(e.get('DielectricConstant_Dk'))
                        tand = float(e.get('LossTangent_Df') or 0.02)
                elif tag(e) == 'Conductor' and e.get('CopperWeight_oz_ft2'):
                    oz = float(e.get('CopperWeight_oz_ft2'))
            mats[spec.get('SpecName'), mat.get('MaterialName')] = (t, er, tand, oz)
    stackup = next((e for e in eda.iter() if tag(e) == 'Stackup'), None)
    if stackup is None:
        return None
    out = []
    for lay in (e for e in stackup.iter() if tag(e) == 'Layer'):
        name, ltype = (lay.get('LayerName') or '').lower(), (lay.get('LayerType') or '').upper()
        ref = next((e for e in lay.iter() if tag(e) == 'SpecRef'), None)
        m = next((e for e in ref.iter() if tag(e) == 'Material'), None) if ref is not None else None
        t, er, tand, oz = mats.get((ref.get('MaterialSpecName') if ref is not None else None,
                                    m.get('MaterialName') if m is not None else None),
                                   (None, None, None, None))
        if name in copper:
            out.append(Layer(name, 'copper', t if t else 0.0347 * (oz or 1.0)))
        elif ltype == 'DIELECTRIC' and t:
            out.append(Layer(name, 'dielectric', t, er or 4.4, tand if tand is not None else 0.02))
    if sorted(l.name for l in out if l.kind == 'copper') != sorted(copper):
        return None
    return out


def _attr_stack(board_layers, copper, attrs):
    """The stack-up from the matrix's layer order and the layers' attributes
    (.layer_dielectric, .dielectric_constant, .loss_tangent, .copper_weight).
    Some writers (Altium) put no DIELECTRIC rows in the matrix and give each
    copper layer the .layer_dielectric below it instead."""
    stack, notes = [], []
    below = {}                                  # copper layer -> its dielectric below
    for l in board_layers:
        name, kind = l['NAME'].lower(), l.get('TYPE', '').upper()
        a = attrs(name)
        mm = a.get('UNITS', 'INCH').upper() == 'MM'
        unit = 1.0 if mm else 25.4
        num = lambda k, d: float(a.get(k) or d)
        if name in copper:
            w = num('.COPPER_WEIGHT', 0.0)      # um in MM jobs, oz/ft^2 in INCH jobs
            stack.append(Layer(name, 'copper', (w / 1000 if mm else 0.0347 * w) if w else 0.035))
            if a.get('.LAYER_DIELECTRIC'):
                below[name] = Layer(f'dielectric below {name}', 'dielectric',
                                    num('.LAYER_DIELECTRIC', 0) * unit,
                                    num('.DIELECTRIC_CONSTANT', 4.4), num('.LOSS_TANGENT', 0.02))
        elif kind == 'DIELECTRIC':
            t = num('.LAYER_DIELECTRIC', 0) * unit
            stack.append(Layer(name, 'dielectric', t or 0.1,
                               num('.DIELECTRIC_CONSTANT', 4.4), num('.LOSS_TANGENT', 0.02)))
            if not t:
                notes.append(f'{name}: no thickness given, 0.1 mm assumed')
    out = []
    for l in stack:                             # fill gaps between copper layers
        if l.kind == 'copper' and out and out[-1].kind == 'copper':
            up = out[-1].name
            if up in below and below[up].thickness > 0:
                out.append(below[up])
            else:
                out.append(Layer(f'dielectric ({up}/{l.name})', 'dielectric', 0.1))
                notes.append(f'no dielectric between {up} and {l.name}: 0.1 mm, er 4.4 assumed')
        out.append(l)
    return out, notes


def _plane_net(name, attrs, nets, via_votes):
    """The net of a negative plane layer. ODB++ does not record it, so: the
    net named in .eda_layers (Altium writes "GroundPlane1(GND)"), else the
    net most of whose holes through the layer land on copper (no clearance
    there), else a net named in the layer name (INT1__GND_)."""
    norm = lambda s: re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')
    by_norm = {norm(n): n for n in nets if n}
    m = re.search(r'\(([^)]+)\)', attrs.get('.EDA_LAYERS', ''))
    if m and norm(m.group(1)) in by_norm:
        return by_norm[norm(m.group(1))]
    if via_votes:
        return max(via_votes, key=via_votes.get)
    parts = norm(name).split('_')
    for n in sorted(by_norm, key=len, reverse=True):
        k = n.split('_')
        if any(parts[i:i + len(k)] == k for i in range(len(parts))):
            return by_norm[n]
    return ''


def read_odbpp(path, step=None):
    """Read an ODB++ job (directory or archive) into a Board. `step`: the step
    to read (default: the first, usually the board)."""
    notes = []
    with _job_root(path) as job:
        steps, mlayers = _matrix(_text(os.path.join(job, 'matrix', 'matrix')) or '')
        step = step or (steps[0] if steps else '')
        sdir = _ci(os.path.join(job, 'steps'), step)
        if sdir is None:
            raise FileNotFoundError(f'step {step!r} not found')
        board_layers = [l for l in mlayers if l.get('CONTEXT', '').upper() == 'BOARD']
        is_cu = lambda l: l.get('TYPE', '').upper() in COPPER_TYPES
        copper = [l['NAME'].lower() for l in board_layers if is_cu(l)]
        if not copper:
            raise ValueError('no copper layers in the matrix')

        def lfile(name, fname):
            """A file of a layer's folder (None if the layer has no folder)."""
            d = _ci(os.path.join(sdir, 'layers'), name)
            return _text(os.path.join(d, fname)) if d else None
        attrs = lambda name: _keyvals(lfile(name, 'attrlist'))

        # drill layers: their spans; copper outside every span is not in the board
        drills = []
        for l in board_layers:
            if l.get('TYPE', '').upper() == 'DRILL':
                top = (l.get('START_NAME') or copper[0]).lower()
                bot = (l.get('END_NAME') or copper[-1]).lower()
                if top not in copper or bot not in copper:
                    top, bot = copper[0], copper[-1]
                drills.append((l['NAME'].lower(), top, bot))
        if drills:
            lo = min(min(copper.index(t), copper.index(b)) for _, t, b in drills)
            hi = max(max(copper.index(t), copper.index(b)) for _, t, b in drills)
            dropped = copper[:lo] + copper[hi + 1:]
            if dropped:
                notes.append(f'left out copper layers outside every drill span: {", ".join(dropped)}')
                copper = copper[lo:hi + 1]
                board_layers = [l for l in board_layers if not is_cu(l) or l['NAME'].lower() in copper]

        # stack-up: stackup.xml if there is one, else the matrix and attributes
        stack = _stackup_xml(_ci(os.path.join(job, 'matrix'), 'stackup.xml'), copper)
        if stack:
            notes.append('stack-up read from matrix/stackup.xml')
        else:
            stack, more = _attr_stack(board_layers, copper, attrs)
            notes += more

        netmap = _nets(_text(os.path.join(sdir, 'eda', 'data')))
        parser = _Features(job)

        # vias: drill layers' holes, spanning START_NAME..END_NAME
        vias = []
        for name, top, bot in drills:
            for idx, g, pos in parser.parse(lfile(name, 'features') or ''):
                c = g.centroid
                d = 2 * math.sqrt(g.area / math.pi)
                vias.append(Via(c.x, -c.y, d, d + 0.2, top, bot, netmap.get((name, idx), '')))

        # outline: the step profile's surface
        prof = parser.parse(_text(os.path.join(sdir, 'profile')) or '')
        outline_up = shapely.union_all([g for _, g, p in prof if p]) if prof else None

        # copper, per net. Features apply in file order: positive adds, negative
        # clears. A NEGATIVE layer is the reverse: the profile is copper and
        # its features (antipads, thermal rings) clear it.
        polarity = {l['NAME'].lower(): l.get('POLARITY', 'POSITIVE').upper() for l in board_layers}
        cu = {}
        for name in copper:
            feats = parser.parse(lfile(name, 'features') or '')
            neg_layer = polarity.get(name) == 'NEGATIVE'
            if neg_layer:
                if outline_up is None:
                    notes.append(f'{name}: negative layer but no profile; left empty')
                    cu[name] = {}
                    continue
                feats = [(-1, outline_up, True)] + [(i, g, not p) for i, g, p in feats]
            acc, pending, negs = defaultdict(list), defaultdict(list), []

            def flush():
                for n, gs in pending.items():
                    acc[n] = [shapely.union_all(acc[n] + gs)]
                pending.clear()

            def cut():
                c = shapely.union_all(negs)
                for n in acc:
                    acc[n] = [acc[n][0].difference(c)] if acc[n] else []
                negs.clear()
            for idx, g, pos in feats:
                if pos:
                    if negs:                    # a run of clears ends: cut everything so far
                        flush(); cut()
                    pending[netmap.get((name, idx), '') if idx >= 0 else None].append(g)
                else:
                    negs.append(g)
            flush()
            if negs:
                cut()
            if neg_layer:                       # the background's net
                if outline_up is not None:
                    for n in acc:
                        acc[n] = [acc[n][0].intersection(outline_up)] if acc[n] else []
                plane = acc.pop(None, [])
                votes = defaultdict(int)
                if plane:
                    for v in vias:
                        if (copper.index(v.top) <= copper.index(name) <= copper.index(v.bottom)
                                and v.net and plane[0].contains(Point(v.x, -v.y))):
                            votes[v.net] += 1
                net = _plane_net(name, attrs(name), set(netmap.values()), votes)
                notes.append(f'{name}: negative plane layer, net {net or "unknown"}')
                if plane:
                    acc[net] = [shapely.union_all(acc.get(net, []) + plane)]
            cu[name] = {n: _flip(shp.clean(gs[0])) for n, gs in acc.items() if gs}
        outline = _flip(outline_up) if outline_up is not None else None
    if not any(n for per in cu.values() for n in per):
        notes.append('no net data (eda/data) found: copper has no nets')
    return Board(str(path), stack, cu, vias, outline, notes)
