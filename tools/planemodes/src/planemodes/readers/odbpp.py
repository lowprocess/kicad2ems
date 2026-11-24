"""ODB++ (a job directory, .tgz/.tar or .zip) -> Board, with nets.

Read: matrix/matrix (layer order, types, drill spans), the step's copper
layers (features: lines, pads, arcs, surfaces; negative polarity clears),
drill layers (vias), eda/data (which net each feature belongs to), the
dielectric layers' attributes (thickness, er, loss tangent) and the profile
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
from collections import defaultdict
from contextlib import contextmanager

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon

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


class _Features:
    """Parses feature files into [(index, geometry mm (y up), positive)].
    Standard symbol sizes are mils (inch files) or microns (mm files);
    user-defined symbols are read from the job's symbols/ directory."""

    def __init__(self, job):
        self.job, self.cache = job, {}

    def symbol(self, name, size_unit):
        key = (name, size_unit)
        if key in self.cache:
            return self.cache[key]
        n = [float(x) * size_unit for x in re.findall(r'[\d.]+', name)]
        g = None
        if re.fullmatch(r'r[\d.]+', name):
            g = Point(0, 0).buffer(n[0] / 2, quad_segs=8)
        elif re.fullmatch(r's[\d.]+', name):
            g = shp.pad_shape('rect', n[0], n[0])
        elif name.startswith('rect') and len(n) >= 2:
            rr = n[2] / min(n[:2]) if 'xr' in name and len(n) > 2 else 0.0
            g = shp.pad_shape('roundrect' if rr else 'rect', n[0], n[1], rr)
        elif name.startswith('oval') and len(n) >= 2:
            g = shp.pad_shape('oval', n[0], n[1])
        elif name.startswith('di') and len(n) >= 2:
            g = shp.pad_shape('diamond', n[0], n[1])
        elif name.startswith('oct') and len(n) >= 2:
            g = shp.pad_shape('octagon', n[0], n[1])
        elif name.startswith(('donut_r', 'thr', 'ths', 's_th', 'donut_s')) and len(n) >= 2:
            # rings and thermal reliefs: an annulus (a thermal's spokes ignored)
            outer = Point(0, 0).buffer(n[0] / 2, quad_segs=8)
            g = outer.difference(Point(0, 0).buffer(n[1] / 2, quad_segs=8))
        elif name.startswith('el') and len(n) >= 2:
            g = affinity.scale(Point(0, 0).buffer(0.5, quad_segs=8), n[0], n[1])
        else:                                   # a user-defined symbol
            d = _ci(os.path.join(self.job, 'symbols'), name)
            text = _text(os.path.join(d, 'features')) if d else None
            if text is not None:
                feats = self.parse(text)
                g = shapely.Polygon()
                for _, geom, pos in feats:
                    g = g.union(geom) if pos else g.difference(geom)
        self.cache[key] = g
        return g

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
        copper = [l['NAME'].lower() for l in board_layers
                  if l.get('TYPE', '').upper() in COPPER_TYPES]
        if not copper:
            raise ValueError('no copper layers in the matrix')
        def lfile(name, fname):
            """A file of a layer's folder (None if the layer has no folder)."""
            d = _ci(os.path.join(sdir, 'layers'), name)
            return _text(os.path.join(d, fname)) if d else None

        # stack-up: copper and dielectric layers in matrix order
        stack = []
        for l in board_layers:
            name, kind = l['NAME'].lower(), l.get('TYPE', '').upper()
            a = _keyvals(lfile(name, 'attrlist'))
            unit = _UNITS.get(a.get('UNITS', 'INCH').upper(), _UNITS['INCH'])[0]
            if kind in COPPER_TYPES:
                oz = float(a.get('.COPPER_WEIGHT', 1) or 1)
                stack.append(Layer(name, 'copper', 0.0347 * oz))
            elif kind == 'DIELECTRIC':
                t = float(a.get('.LAYER_DIELECTRIC', 0) or 0) * unit
                stack.append(Layer(name, 'dielectric', t or 0.1,
                                   float(a.get('.DIELECTRIC_CONSTANT', 4.4) or 4.4),
                                   float(a.get('.LOSS_TANGENT', 0.02) or 0.02)))
        # a gap with no dielectric layer gets an assumed one
        out, prev = [], None
        for l in stack:
            if l.kind == 'copper' and prev == 'copper':
                out.append(Layer(f'dielectric ({out[-1].name}/{l.name})', 'dielectric', 0.1))
                notes.append(f'no dielectric between {out[-2].name} and {l.name}: 0.1 mm, er 4.4 assumed')
            out.append(l)
            prev = l.kind
        stack = out

        # nets of the features
        netmap = _nets(_text(os.path.join(sdir, 'eda', 'data')))
        parser = _Features(job)
        cu = {}
        for name in copper:
            feats = parser.parse(lfile(name, 'features') or '')
            acc, pending = defaultdict(list), defaultdict(list)

            def flush():
                for n, gs in pending.items():
                    acc[n] = [shapely.union_all(acc[n] + gs)]
                pending.clear()
            negs = []
            for idx, g, pos in feats:
                if pos:
                    if negs:                    # a run of clears ends: cut everything so far
                        flush()
                        cut = shapely.union_all(negs)
                        for n in acc:
                            acc[n] = [acc[n][0].difference(cut)] if acc[n] else []
                        negs = []
                    pending[netmap.get((name, idx), '')].append(g)
                else:
                    negs.append(g)
            flush()
            if negs:
                cut = shapely.union_all(negs)
                for n in acc:
                    acc[n] = [acc[n][0].difference(cut)] if acc[n] else []
            cu[name] = {n: _flip(shp.clean(gs[0])) for n, gs in acc.items() if gs}

        # vias: drill layers' holes, spanning START_NAME..END_NAME
        vias = []
        for l in board_layers:
            if l.get('TYPE', '').upper() != 'DRILL':
                continue
            name = l['NAME'].lower()
            top = (l.get('START_NAME') or copper[0]).lower()
            bot = (l.get('END_NAME') or copper[-1]).lower()
            if top not in copper or bot not in copper:
                top, bot = copper[0], copper[-1]
            for idx, g, pos in parser.parse(lfile(name, 'features') or ''):
                c = g.centroid
                d = 2 * math.sqrt(g.area / math.pi)
                vias.append(Via(c.x, -c.y, d, d + 0.2, top, bot, netmap.get((name, idx), '')))

        # outline: the step profile's surface
        prof = parser.parse(_text(os.path.join(sdir, 'profile')) or '')
        outline = _flip(shapely.union_all([g for _, g, p in prof if p])) if prof else None
    if not any(n for per in cu.values() for n in per):
        notes.append('no net data (eda/data) found: copper has no nets')
    return Board(str(path), stack, cu, vias, outline, notes)
