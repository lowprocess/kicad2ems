"""HyperLynx .hyp -> Board.

The format (pcb-rnd's io_hyp grammar and hyp2mat are the references): sections
{NAME ...} holding records (KEY a=b ...), '*' starts a comment line, strings
may be quoted, numbers may carry an SI suffix. Read here: UNITS, PLANE_SEP,
BOARD (perimeter), STACKUP, PADSTACK and each NET's SEG, ARC, VIA, PIN, PAD,
POLYGON / POLYVOID / POLYLINE. Writers are loose (a missing ')' or '}', text after
a record), so a record ends at the next bracket and a top-level section
heading closes whatever is still open.

Units: ENGLISH lengths are inches, METRIC centimetres; with WEIGHT, copper
thickness is oz/ft^2 (ENGLISH) or g/cm^2 (METRIC). Y points up (flipped here).
Arcs: PERIMETER_ARC and CURVE run counter-clockwise from point 1 to 2 (a CURVE
is walked from whichever end meets the previous vertex), net ARCs clockwise.
A polygon's outline is drawn with a pen of width W, so the copper is the
polygon grown by W/2 and a void shrunk by W/2.

PLANE layers: HyperLynx clears a plane polygon (T=PLANE) around everything of
other nets on that layer, by the padstack's antipad or else by the plane
separation (PS= of the layer, or PLANE_SEP). A PLANE layer with no polygons
at all is flooded with the outline for the net most of whose holes connect
to it (thermal or pad there, no antipad).

KiCad's exporter is recognised (its .kicad_pcb named in BOARD, dielectrics
named DE_<layer>) and its quirks undone: pad angles written as 180 -
orientation, ovals as shape 0, rounded rectangles as 2 (a 25 % corner radius
assumed, KiCad's default) and, before KiCad 9, y left pointing down.
"""
import math
import re
from collections import defaultdict

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Point

from ..model import Board, Layer, Via
from . import shapes as shp

_TOK = re.compile(r'"(?:[^"]|"")*"|[{}()=,]|[^\s{}()=,"]+')
_TOP = {'VERSION', 'DATA_MODE', 'UNITS', 'PLANE_SEP', 'BOARD', 'STACKUP', 'DEVICES',
        'SUPPLIES', 'PADSTACK', 'NET', 'NET_CLASS', 'KEY', 'END', 'BOARD_FILE'}
_SI = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, 'k': 1e3, 'M': 1e6, 'G': 1e9}


def _num(s, default=float('nan')):
    """A number, with an optional SI suffix (1.5u)."""
    try:
        return float(s)
    except (TypeError, ValueError):
        m = re.fullmatch(r'([-+.\deE]+)([pnumkMG])', str(s))
        return float(m.group(1)) * _SI[m.group(2)] if m else default


def _unq(t):
    return t[1:-1].replace('""', '"') if t.startswith('"') else t


class _Sec:
    """A {section}: its head word, positional values, KEY=value pairs and
    contents: records (name, positional, keys) and nested ('{', _Sec)."""
    def __init__(self, head, pos, kv):
        self.head, self.pos, self.kv, self.recs = head, pos, kv, []


def _parse(text):
    """The file's top-level sections, [_Sec]."""
    text = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('*'))
    toks = _TOK.findall(text)
    n = len(toks)

    def args(j):
        """Positional values and KEY=value pairs from toks[j] up to the next bracket."""
        pos, kv = [], {}
        while j < n and toks[j] not in '(){}':
            t = toks[j]
            if j + 2 < n and toks[j + 1] == '=' and toks[j + 2] not in '(){}':
                kv[t.upper()] = _unq(toks[j + 2]); j += 3
            elif t in '=,':
                j += 1
            else:
                pos.append(_unq(t)); j += 1
        return pos, kv, j

    top, stack, i = [], [], 0
    while i < n:
        t = toks[i]
        if t == '{' and i + 1 < n:
            head = toks[i + 1].upper()
            j = i + 2
            pos, kv, i = args(j + 1 if j < n and toks[j] == '=' else j)  # {NET=name ...}
            sec = _Sec(head, pos, kv)
            if head in _TOP:                   # a top-level section closes any left open
                stack.clear()
            if stack:
                stack[-1].recs.append(('{', sec))
            else:
                top.append(sec)
            stack.append(sec)
        elif t == '}':
            if stack:
                stack.pop()
            i += 1
        elif t == '(' and i + 1 < n:
            name = _unq(toks[i + 1])
            pos, kv, i = args(i + 2)
            if stack:
                stack[-1].recs.append((name, pos, kv))
        else:                                   # ')' and stray text after records
            i += 1
    return top


def read_hyp(path):
    """Read a HyperLynx .hyp file into a Board."""
    with open(path, encoding='latin-1') as fh:
        secs = _parse(fh.read())
    first = lambda head: next((s for s in secs if s.head == head), None)
    notes = []
    u = first('UNITS')
    words = ' '.join((u.pos + list(u.kv.values())) if u else []).upper()
    k = 10.0 if 'METRIC' in words else 25.4            # file length unit -> mm
    # KiCad's exporter: its file name in BOARD, dielectrics named DE_<layer>
    b = first('BOARD')
    kicad = bool(b and b.pos and b.pos[0].lower().endswith('.kicad_pcb')) or any(
        n.upper() == 'DIELECTRIC' and re.match(r'DE\d*_', kv.get('L', ''))
        for n, _, kv in (first('STACKUP').recs if first('STACKUP') else []))
    # y points up, flipped here as the Board's points down; KiCad before 9
    # wrote its own y (down, positive) unflipped
    ys = [_num(kv[c]) for _, _, kv in (b.recs if b else []) for c in ('Y1', 'Y2') if c in kv]
    ysign = 1.0 if kicad and ys and np.nanmean(ys) > 0 else -1.0
    if ysign > 0:
        notes.append('KiCad export with y unflipped (KiCad 8 or older): read as is')
    X = lambda v: _num(v) * k
    Y = lambda v: ysign * _num(v) * k
    ps = first('PLANE_SEP')
    plane_sep = X(ps.pos[0]) if ps and ps.pos else 0.0

    # ---- stack-up, top to bottom --------------------------------------------------
    stack, planes, sep = [], set(), {}
    for name, pos, kv in (first('STACKUP').recs if first('STACKUP') else []):
        name = name.upper()
        if name in ('SIGNAL', 'PLANE'):
            t = _num(kv.get('T'), 0.0014)
            if 'WEIGHT' in words:                       # copper weight, not thickness
                t *= 1.116 if 'METRIC' in words else 0.0347
            else:
                t *= k
            lname = kv.get('L', f'L{len(stack)}')
            stack.append(Layer(lname, 'copper', t))
            if name == 'PLANE':
                planes.add(lname)
                sep[lname] = X(kv['PS']) if 'PS' in kv else plane_sep
        elif name == 'DIELECTRIC':                      # here C= is the permittivity
            stack.append(Layer(kv.get('L', f'D{len(stack)}'), 'dielectric',
                               _num(kv.get('T'), 0.004) * k,
                               _num(kv.get('ER', kv.get('C')), 4.3), _num(kv.get('LT'), 0.02)))
    cu = [l.name for l in stack if l.kind == 'copper']
    if not cu:
        raise ValueError('no copper layers in the STACKUP')

    # ---- outline --------------------------------------------------------------------
    segs = []
    for name, pos, kv in (first('BOARD').recs if first('BOARD') else []):
        if not {'X1', 'Y1', 'X2', 'Y2'} <= kv.keys():
            continue
        p1, p2 = (X(kv['X1']), Y(kv['Y1'])), (X(kv['X2']), Y(kv['Y2']))
        if name.upper() == 'PERIMETER_SEGMENT':
            segs.append([p1, p2])
        elif name.upper() == 'PERIMETER_ARC':           # CCW with y up = CW once flipped
            segs.append(shp.arc_points(p1, p2, (X(kv['XC']), Y(kv['YC'])), cw=True))
    outline = shp.polygonize_outline(segs)

    # ---- padstacks: name -> (drill mm, [(layer, shape, sx, sy, angle, M|A|T)]) ---------
    pads = {}
    for s in secs:
        if s.head != 'PADSTACK':
            continue
        ent = []
        for name, p, _ in s.recs:
            v = [name] + p
            if len(v) >= 5:
                kind = v[-1].upper() if v[-1].upper() in ('M', 'A', 'T') else 'M'
                ent.append((v[0], v[1], X(v[2]), X(v[3]), _num(v[4], 0.0), kind))
        drill = X(s.pos[1]) if len(s.pos) > 1 else 0.0
        pads[s.pos[0] if s.pos else ''] = (drill if drill == drill else 0.0, ent)

    def shape(sh, sx, sy, x, y, ang):
        """One pad shape: 0 ellipse, 1 rectangle, 2 oblong (KiCad writes its
        circles and ovals as 0, rounded rectangles as 2); `ang` CCW as seen,
        which place() applies. KiCad writes 180 - its pad orientation."""
        if kicad:                               # KiCad: 0 its circle / oval, 2 roundrect
            g = shp.pad_shape({'0': 'oval', '2': 'roundrect'}.get(sh, 'rect'), sx, sy, 0.25)
        elif sh == '0':
            g = (shp.pad_shape('circle', sx, sx) if abs(sx - sy) < 1e-9 else
                 affinity.scale(Point(0, 0).buffer(0.5, quad_segs=8), sx, sy))
        else:
            g = shp.pad_shape('oval' if sh == '2' else 'rect', sx, sy)
        return shp.place(g, x, y, 180.0 - ang if kicad else ang)

    def rows(name, layer, kinds):
        """A padstack's rows of the given kinds on `layer`; MDEF / ADEF stand in
        for layers without an explicit row of that kind."""
        ent = pads.get(name, (0.0, []))[1]
        mine = [e for e in ent if e[0] == layer and e[5] in kinds]
        if mine:
            return mine
        default = {'M': 'MDEF', 'A': 'ADEF'}
        return [e for e in ent if e[5] in kinds and e[0] == default.get(e[5])]

    def metal(name, x, y):
        """{layer: geometry} of a padstack's metal pads at (x, y)."""
        out = {}
        for l in cu:
            r = [e for e in rows(name, l, 'M') if e[2] > 0]
            if r:
                out[l] = shape(r[0][1], r[0][2], r[0][3], x, y, r[0][4])
        return out

    # ---- nets ---------------------------------------------------------------------
    parts = defaultdict(list)                  # (layer, net) -> [geometry]
    plane_polys = defaultdict(list)            # (layer, net) -> [T=PLANE polygons]
    holes = []                                 # (x, y, padstack, net) of drilled pins / vias
    vias = []
    for s in secs:
        if s.head != 'NET':
            continue
        net = s.pos[0] if s.pos else ''
        if kicad and re.fullmatch(r'EmptyNet\d+', net):
            net = ''                           # KiCad's name for pads on no net
        polys, voids = {}, defaultdict(list)
        for rec in s.recs:
            if rec[0] == '{':                  # POLYGON / POLYVOID / POLYLINE
                b = rec[1]
                pts = [(X(b.kv['X']), Y(b.kv['Y']))] if 'X' in b.kv and 'Y' in b.kv else []
                for rn, *rest in b.recs:
                    ra = rest[-1] if rest and isinstance(rest[-1], dict) else {}
                    rn = rn.upper()
                    if rn == 'LINE':
                        pts.append((X(ra['X']), Y(ra['Y'])))
                    elif rn == 'CURVE':
                        a1, a2 = (X(ra['X1']), Y(ra['Y1'])), (X(ra['X2']), Y(ra['Y2']))
                        arc = shp.arc_points(a1, a2, (X(ra['XC']), Y(ra['YC'])), cw=True)
                        if pts and math.dist(pts[-1], a2) < math.dist(pts[-1], a1):
                            arc = arc[::-1]
                        pts.extend(map(tuple, arc))
                w, lay, pid = X(b.kv.get('W', '0')), b.kv.get('L'), b.kv.get('ID')
                if b.head == 'POLYLINE' and lay in cu and len(pts) > 1:
                    parts[lay, net].append(shp.stroke(np.array(pts), max(w, 1e-3)))
                elif b.head == 'POLYGON' and len(pts) >= 3:
                    polys[pid] = (lay, shp.ring_polygon(pts), w, b.kv.get('T', 'POUR').upper())
                elif b.head == 'POLYVOID' and len(pts) >= 3:
                    voids[pid].append(shp.ring_polygon(pts))
                continue
            name, _, a = rec
            name = name.upper()
            if name in ('SEG', 'ARC') and a.get('L') in cu:
                p1, p2 = (X(a['X1']), Y(a['Y1'])), (X(a['X2']), Y(a['Y2']))
                pts = (np.array([p1, p2]) if name == 'SEG'          # ARC: CW with y up
                       else shp.arc_points(p1, p2, (X(a['XC']), Y(a['YC'])), cw=False))
                parts[a['L'], net].append(shp.stroke(pts, X(a.get('W', '0'))))
            elif name in ('VIA', 'PIN') and 'P' in a:
                x, y = X(a['X']), Y(a['Y'])
                geo = metal(a['P'], x, y)
                drill = pads.get(a['P'], (0.0, []))[0]
                if drill > 0 and all(g.bounds[2] - g.bounds[0] <= drill * 1.001 for g in geo.values()):
                    continue                   # metal no wider than the hole: unplated
                for l, g in geo.items():
                    parts[l, net].append(g)
                if drill > 0:                  # plated hole: spans its pads' layers
                    on = sorted(cu.index(l) for l in geo) or [0, len(cu) - 1]
                    pad = max((g.bounds[2] - g.bounds[0] for g in geo.values()), default=drill)
                    vias.append(Via(x, y, drill, pad, cu[on[0]], cu[on[-1]], net))
                    holes.append((x, y, a['P'], net))
            elif name == 'VIA' and 'D' in a:   # the old form: D= drill, L1= .. L2=
                l1, l2 = a.get('L1', cu[0]), a.get('L2', cu[-1])
                i, j = sorted((cu.index(l1) if l1 in cu else 0,
                               cu.index(l2) if l2 in cu else len(cu) - 1))
                vias.append(Via(X(a['X']), Y(a['Y']), X(a['D']), X(a['D']), cu[i], cu[j], net))
            elif name == 'PAD' and a.get('L') in cu:   # the old form: one pad
                sh = {'RECT': '1', 'OBLONG': '2'}.get(a.get('S', 'OVAL').upper(), '0')
                parts[a['L'], net].append(shape(sh, X(a.get('SX', '0')), X(a.get('SY', '0')),
                                                X(a['X']), Y(a['Y']), _num(a.get('A'), 0.0)))
        for pid, (lay, poly, w, kind) in polys.items():
            if lay not in cu:
                continue
            g = poly.buffer(w / 2) if w > 0 else poly
            for v in voids.get(pid, []):
                g = g.difference(v.buffer(-w / 2) if w > 0 else v)
            (plane_polys if kind == 'PLANE' and lay in planes else parts)[lay, net].append(g)

    copper = defaultdict(dict)
    for (l, n), gs in parts.items():
        copper[l][n] = shapely.union_all([shp.clean(g) for g in gs])

    # ---- PLANE layers: flood empty ones, clear plane polygons round other nets ------------
    def connects(p, layer):
        return bool(rows(p, layer, 'T') or (rows(p, layer, 'M') and not rows(p, layer, 'A')))

    for l in cu:
        if l not in planes:
            continue
        if not copper.get(l) and not any(key[0] == l for key in plane_polys) and outline is not None:
            votes = defaultdict(int)
            for x, y, p, n in holes:
                votes[n] += connects(p, l)
            if votes and max(votes.values()) > 0:
                n = max(votes, key=votes.get)
                plane_polys[l, n].append(outline)
                notes.append(f'plane layer {l}: no polygons, flooded for net {n}')
        for (pl, net), gs in list(plane_polys.items()):
            if pl != l:
                continue
            clear = [g.buffer(sep[l]) for n, g in copper.get(l, {}).items() if n != net]
            for x, y, p, n in holes:
                if n == net and connects(p, l):
                    continue
                a = rows(p, l, 'A')
                if a:
                    clear.append(shape(a[0][1], a[0][2], a[0][3], x, y, a[0][4]))
                else:
                    d = pads[p][0]
                    m = rows(p, l, 'M') or [(l, '0', d, d, 0.0, 'M')]
                    clear.append(shape(m[0][1], m[0][2] + 2 * sep[l], m[0][3] + 2 * sep[l],
                                       x, y, m[0][4]))
            g = shapely.union_all(gs)
            if clear:
                g = g.difference(shapely.union_all(clear))
            old = copper[l].get(net)
            copper[l][net] = shp.clean(g if old is None else old.union(g))
    return Board(str(path), stack, dict(copper), vias, outline, notes)
