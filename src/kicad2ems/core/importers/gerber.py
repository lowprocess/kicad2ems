"""
Gerber (RS-274X / X2) + Excellon + a KiCad job file (.gbrjob): the fabrication
data of a board, imported into the same raw form as the KiCad reader, so
board.Board resolves both alike.

    raw = GerberBoard('fab/board-job.gbrjob', pos='fab/board-top.pos')

  stack    the job file's MaterialStackup (copper, dielectric, solder mask)
  copper   every copper Gerber the job lists: draws with any aperture (the
           convex hull of the start and end apertures), arcs (G02 / G03, G75),
           flashes (C, R, O, P and aperture macros: primitives 1, 4, 5, 20, 21
           with variables and arithmetic - KiCad's RoundRect / RotRect / Outline
           / FreePoly), regions (G36 / G37), dark / clear polarity (LPD / LPC);
           X2 net attributes (TO.N) give the nets
  tracks   draws with a circular aperture (centre line and width), for the line
           model
  vias     the plated drill files; the layer span is the X2 file attribute
           (TF.FileFunction,Plated,<from>,<to>)
  outline  the Profile Gerber (Edge.Cuts), if the job lists one
  ports    the pick-and-place file: components with Value or Reference P<n> (a
           Gerber has no footprint fields: a port on an inner layer is a
           setting, --port P2@x,y,In2.Cu,rotation)
Coordinates are turned into KiCad's (mm, y down), the raw form every importer
produces.
"""
import itertools, json, os, re
import numpy as np
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union
from shapely import affinity
from . import Imported
from .kicad.pnp import read_pos
from .kicad.shapes import oval


# ---- aperture macros
# ----------------------------------------------------------------------------------
def _eval(expr, var):
    e = expr.strip().replace('x', '*').replace('X', '*')
    e = re.sub(r'\$(\d+)', lambda m: repr(var.get(int(m.group(1)), 0.0)), e)
    if not re.fullmatch(r'[0-9eE+\-*/(). ]*', e):
        raise ValueError(f"bad macro expression {expr!r}")
    return float(eval(e, {"__builtins__": {}}, {}))


def _macro_shape(body, params):
    var = {i + 1: p for i, p in enumerate(params)}
    dark, clear = [], []
    for stmt in body:
        stmt = stmt.strip()
        if not stmt or stmt.startswith('0'):
            continue
        if stmt.startswith('$'):
            k, e = stmt[1:].split('=', 1)
            var[int(k)] = _eval(e, var)
            continue
        a = [_eval(x, var) for x in stmt.split(',')]
        code, exp = int(a[0]), a[1]
        if code == 1:  # circle: exp, dia, x, y [, rot]
            g = Point(a[3], a[4]).buffer(a[2] / 2, quad_segs=8)
            if len(a) > 5 and a[5]:
                g = affinity.rotate(g, a[5], origin=(0, 0))
        elif code == 20:  # vector line: exp, w, x1, y1, x2, y2, rot
            g = LineString([(a[3], a[4]),
                            (a[5], a[6])]).buffer(a[2] / 2, cap_style='flat')
            g = affinity.rotate(g, a[7], origin=(0, 0))
        elif code == 21:  # centre line: exp, w, h, x, y, rot
            g = box(a[4] - a[2] / 2, a[5] - a[3] / 2, a[4] + a[2] / 2,
                    a[5] + a[3] / 2)
            g = affinity.rotate(g, a[6], origin=(0, 0))
        elif code == 4:  # outline: exp, n, x0, y0, ..., rot
            n = int(a[2])
            pts = [(a[3 + 2 * i], a[4 + 2 * i]) for i in range(n + 1)]
            g = affinity.rotate(
                Polygon(pts).buffer(0), a[3 + 2 * (n + 1)], origin=(0, 0))
        elif code == 5:  # polygon: exp, n, x, y, dia, rot
            n = int(a[2]); r = a[5] / 2
            t = np.radians(a[6]) + 2 * np.pi * np.arange(n) / n
            g = Polygon(
                np.column_stack([a[3] + r * np.cos(t), a[4] + r * np.sin(t)]))
        else:
            continue  # moire / thermal: not used for copper
        (dark if exp else clear).append(g)
    g = unary_union(dark) if dark else Point(0, 0).buffer(1e-6)
    return g.difference(unary_union(clear)) if clear else g


# ---- one Gerber file
# -------------------------------------------------------------------------------------
def parse_gerber(path):
    """(copper {net: geometry}, draws [(points, round aperture diameter or None,
    net)], X2 file attributes), in mm with y up (Gerber's own orientation)."""
    text = open(path, encoding='utf-8', errors='replace').read()
    attrs, macros, aps, ap_round = {}, {}, {}, {}
    fmt, unit = (4, 6), 1.0
    x = y = 0.0
    interp = 'G01'
    cur_ap, region, region_pts = None, False, []
    net, polarity = '', 'D'
    # ops: ('D', geometry, net) / ('C', geometry, None), in order
    ops, draws = [], []

    def coord(v):
        if v is None:
            return None
        if '.' in v:
            return float(v) * unit
        val = int(v.lstrip('+-')) / 10 ** fmt[1]
        return (-val if v.startswith('-') else val) * unit

    def add(g):
        ops.append((polarity, g, net))

    for m in re.finditer(r'%([^%]*)%|([^%*]+)\*', text, re.S):
        if m.group(1) is not None:
            blk = m.group(1).strip()
            if blk.startswith('FS'):
                mm = re.search(r'X(\d)(\d)Y(\d)(\d)', blk)
                fmt = (int(mm.group(1)), int(mm.group(2)))
            elif blk.startswith('MO'):
                unit = 25.4 if 'IN' in blk else 1.0
            elif blk.startswith('TF'):
                parts = blk[2:].rstrip('*').split(',')
                attrs[parts[0].lstrip('.')] = parts[1:]
            elif blk.startswith('TO.N,'):
                net = blk[5:].rstrip('*').split(',')[0]
            elif blk.startswith('TD'):
                if blk.rstrip('*') in ('TD', 'TD.N'):
                    net = ''
            elif blk.startswith('AM'):
                name, *body = blk[2:].split('*')
                macros[name.strip()] = [b for b in body if b.strip()]
            elif blk.startswith('AD'):
                mm = re.match(r'ADD(\d+)([A-Za-z_.$][\w.$\-]*)(?:,(.*))?\*?$',
                              blk.rstrip('*'), re.S)
                code, name, par = int(mm.group(1)), mm.group(2), mm.group(3)
                p = [float(v) * unit for v in par.split('X')] if par else []
                if name == 'C':
                    g = Point(0, 0).buffer(p[0] / 2, quad_segs=8)
                    ap_round[code] = p[0]
                elif name == 'R':
                    g = box(-p[0] / 2, -p[1] / 2, p[0] / 2, p[1] / 2)
                elif name == 'O':
                    g = oval(p[0], p[1])
                elif name == 'P':
                    n = int(p[1] / unit); rot = p[2] / unit if len(p) > 2 else 0
                    t = np.radians(rot) + 2 * np.pi * np.arange(n) / n
                    g = Polygon(
                        np.column_stack(
                            [p[0] / 2 * np.cos(t), p[0] / 2 * np.sin(t)]))
                else:
                    g = _macro_shape(macros[name],
                                     ([float(v)
                                       for v in par.split('X')] if par else []))
                    if unit != 1.0:
                        g = affinity.scale(g, unit, unit, origin=(0, 0))
                aps[code] = g
            elif blk.startswith('LP'):
                polarity = blk[2]
            continue
        st = m.group(2).strip()
        if not st:
            continue
        for g in re.findall(r'G0?[123]\b|G7[45]|G3[67]', st):
            g = g if len(g) == 3 else 'G0' + g[1]
            if g in ('G01', 'G02', 'G03'):
                interp = g
            elif g == 'G36':
                region, region_pts = True, []
            elif g == 'G37':
                if len(region_pts) >= 3:
                    add(Polygon(region_pts).buffer(0))
                region = False
        dsel = re.fullmatch(r'(?:G54)?D(\d+)', st)
        if dsel and int(dsel.group(1)) >= 10:
            cur_ap = int(dsel.group(1)); continue
        mm = re.search(
            r'(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?(?:I([+-]?[\d.]+))?(?:'
            r'J([+-]?[\d.]+))?D0?([123])',
            st)
        if not mm:
            continue
        nx, ny = coord(mm.group(1)), coord(mm.group(2))
        nx = x if nx is None else nx
        ny = y if ny is None else ny
        i_, j_ = coord(mm.group(3)) or 0.0, coord(mm.group(4)) or 0.0
        d = mm.group(5)
        if d == '1':
            if interp == 'G01':
                pts = [(x, y), (nx, ny)]
            else:
                c = np.array([x + i_, y + j_])
                a0, a1 = (np.arctan2(y - c[1], x - c[0]),
                          np.arctan2(ny - c[1], nx - c[0]))
                if interp == 'G02':                          # clockwise
                    sw = (a1 - a0) % (2 * np.pi) - 2 * np.pi
                    sw = -2 * np.pi if abs(sw) < 1e-12 else sw
                else:
                    sw = (a1 - a0) % (2 * np.pi)
                    sw = 2 * np.pi if abs(sw) < 1e-12 else sw
                r = np.hypot(x - c[0], y - c[1])
                t = (a0 + sw * np.linspace(0, 1,
                                           max(4, int(abs(sw) * r / 0.05)) + 1))
                pts = list(zip(c[0] + r * np.cos(t), c[1] + r * np.sin(t)))
            if region:
                region_pts = (region_pts or [pts[0]]) + pts[1:]
            elif cur_ap is not None:
                ap = aps[cur_ap]
                add(
                    unary_union([
                        unary_union([
                            affinity.translate(ap, xa, ya),
                            affinity.translate(ap, xb, yb)
                        ]).convex_hull
                        for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:])
                    ]))
                draws.append((pts, ap_round.get(cur_ap), net))
        elif d == '2':
            if region and len(region_pts) >= 3:
                add(Polygon(region_pts).buffer(0))
            region_pts = [(nx, ny)] if region else []
        elif d == '3' and cur_ap is not None:
            add(affinity.translate(aps[cur_ap], nx, ny))
        x, y = nx, ny
    copper = {}
    # runs of one polarity, in order
    for pol, run in itertools.groupby(ops, key=lambda op: op[0]):
        if pol == 'D':
            by = {}
            for _, g, n in run:
                by.setdefault(n, []).append(g)
            for n, gs in by.items():
                copper[n] = (unary_union([copper[n]] + gs)
                             if n in copper else unary_union(gs))
        else:
            clear = unary_union([g for _, g, _ in run])
            copper = {n: g.difference(clear) for n, g in copper.items()}
    return ({n: g.buffer(0)
             for n, g in copper.items()
             if not g.is_empty}, draws, attrs)


def parse_excellon(path):
    """Holes [(x, y, diameter)] in mm (y up), the X2 layer span (from, to) or
    None, and whether plated."""
    text = open(path, encoding='utf-8', errors='replace').read()
    unit, span = 1.0, None
    m = re.search(r'TF\.FileFunction,(?:Plated|NonPlated),(\d+),(\d+)', text)
    if m:
        span = (int(m.group(1)), int(m.group(2)))
    plated = ('NonPlated' not in text
              and 'NPTH' not in os.path.basename(path).upper())
    tools, holes, tool = {}, [], None
    # (TZ: leading zeros left out; LZ: trailing; KiCad writes decimals)
    zeros, digits = 'TZ', None
    last = [0.0, 0.0]
    for line in text.splitlines():
        line = line.strip()
        mu = re.match(r'(INCH|METRIC)(?:,(LZ|TZ))?(?:,(0+)\.(0+))?', line)
        if mu:
            unit = 25.4 if mu.group(1) == 'INCH' else 1.0
            zeros = mu.group(2) or zeros
            digits = ((len(mu.group(3)),
                       len(mu.group(4))) if mu.group(3) else digits)
            continue
        # T1C0.3, or T1F00S00C0.300 (fields first)
        mt = re.match(r'T(\d+)(?:[FSBHZ][\d.]*)*C([\d.]+)', line)
        if mt:
            tools[int(mt.group(1))] = float(mt.group(2)) * unit; continue
        ms = re.fullmatch(r'T(\d+)', line)
        if ms:
            tool = int(ms.group(1)); continue
        mx = re.match(r'(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?$', line)
        if mx and (mx.group(1) or mx.group(2)) and tool is not None:
            whole, frac = digits or ((2, 4) if unit != 1.0 else (3, 3))
            def num(v):
                if '.' in v:
                    return float(v)
                sign = -1.0 if v.startswith('-') else 1.0
                v = v.lstrip('+-')
                # leading zeros kept: the trailing ones were left out
                if zeros == 'LZ':
                    v = v.ljust(whole + frac, '0')
                return sign * int(v) / 10 ** frac
            for k, v in enumerate((mx.group(1), mx.group(2))):
                if v:
                    # (a coordinate left out keeps its last value)
                    last[k] = num(v) * unit
            holes.append((last[0], last[1], tools.get(tool, 0.3)))
    return holes, span, plated


# ---- the board
# ---------------------------------------------------------------------------------------------
class GerberBoard(Imported):
    importer = 'gerber'

    def __init__(self, job, pos=None, drills=None):
        super().__init__(job)
        d = os.path.dirname(job)
        J = json.load(open(job, encoding='utf-8'))
        for it in J.get('MaterialStackup', []):
            t, th = it.get('Type', ''), float(it.get('Thickness', 0)) * 1e-3
            if t == 'Copper':
                self.stack_items.append(
                    dict(kind='copper', name=it['Name'], t=th))
                self.copper_order.append(it['Name'])
            elif t in ('Dielectric', 'SolderMask'):
                mask = t == 'SolderMask'
                self.stack_items.append(
                    dict(
                        kind='mask' if mask else 'diel',
                        name=it.get('Name', t.lower()), t=th, er=float(
                            it.get('DielectricConstant', 3.3 if mask else 4.5)),
                        tand=float(it.get('LossTangent',
                                          0.0 if mask else 0.02))))
        # Gerber y up -> KiCad y down
        flip = lambda g: affinity.scale(g, 1, -1, origin=(0, 0))
        for fa in J.get('FilesAttributes', []):
            ff = fa.get('FileFunction', '')
            path = os.path.join(d, fa['Path'])
            if ff.startswith('Profile'):
                for pts, _, _ in parse_gerber(path)[1]:
                    self.edges.append(LineString([(px, -py) for px, py in pts]))
            m = re.match(r'Copper,L(\d+)', ff)
            if not m:
                continue
            li = int(m.group(1)) - 1
            name = (self.copper_order[li]
                    if li < len(self.copper_order) else f"L{li + 1}")
            copper, draws, _ = parse_gerber(path)
            for net, g in copper.items():
                self.items.append((name, net, flip(g)))
                self.shapes.append(self.items[-1])
            for pts, dia, net in draws:
                if dia:
                    self.tracks.append(
                        dict(layer=name, net=net, width=dia,
                             pts=np.array([(px, -py) for px, py in pts])))
        drills = (drills if drills is not None else [
            os.path.join(d, f)
            for f in sorted(os.listdir(d))
            if f.lower().endswith(('.drl', '.xln', '.exc'))
        ])
        for f in drills:
            holes, span, plated = parse_excellon(f)
            if plated:
                lys = (self.copper_order[span[0] - 1:span[1]]
                       if span else self.copper_order)
                for hx, hy, dia in holes:
                    net = self._net_at(lys, hx, -hy)
                    self.vias.append(
                        dict(x=hx, y=-hy, size=0, drill=dia, layers=lys,
                             net=net))
        for r in (read_pos(pos) if pos else []):
            self.footprints.append(
                dict(ref=r['ref'], value=r['val'], fab=[], props={}, x=r['x'],
                     y=-r['y'], rot=r['rot'],
                     side=('B' if r['side'].lower().startswith('b') else 'F'),
                     lib=r['package'], pads=[], dnp=False,
                     exclude_from_sim=False))
        passives = [
            f['ref']
            for f in self.footprints
            if f['ref'][:1].upper() in 'RLC' and f['ref'][1:2].isdigit()
        ]
        if passives:  # (they had been left out with nothing said)
            self.notes.append(
                "Gerber import: the placement file's parts have no pads, so "
                f"{len(passives)} lumped part(s) "
                f"({', '.join(passives[:6])}{', ...' if len(passives) > 6 else ''}) "
                "are left out of the model: import the .kicad_pcb for them")

    def _net_at(self, layers, x, y):
        """The net of the copper a drill hole goes through (a Gerber's drill
        files carry none)."""
        pt = Point(x, y)
        return next((n for l, n, g in self.items
                     if l in layers and n and g.contains(pt)), '')
