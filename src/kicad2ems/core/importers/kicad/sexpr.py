"""
KiCad S-expression reader and small node accessors.

    root = parse_sexpr(open('board.kicad_pcb').read())
    for seg in items(root, 'segment'): ...
"""
import re


_TOK = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def parse_sexpr(text):
    stack, cur = [], []
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t == '(':
            stack.append(cur); cur = []
        elif t == ')':
            done = cur; cur = stack.pop(); cur.append(done)
        elif t[0] == '"':
            cur.append(bytes(t[1:-1], 'utf-8').decode('unicode_escape') if '\\' in t else t[1:-1])
        else:
            cur.append(t)
    return cur[0]


def items(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def item(node, name, default=None):
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return default


def val(node, name, default=None, cast=str):
    it = item(node, name)
    return default if it is None or len(it) < 2 else cast(it[1])


def xy(node, name):
    it = item(node, name)
    return None if it is None else (float(it[1]), float(it[2]))


def pts(node):
    """Points of a (pts ...) list: (xy x y) entries and, in KiCad 7+ polygons, (arc (start) (mid) (end))
    entries expanded into points along the arc."""
    from .shapes import arc_points
    p = item(node, 'pts')
    out = []
    for c in (p[1:] if p else []):
        if not isinstance(c, list):
            continue
        if c[0] == 'xy':
            out.append((float(c[1]), float(c[2])))
        elif c[0] == 'arc':
            out += [tuple(q) for q in arc_points(xy(c, 'start'), xy(c, 'mid'), xy(c, 'end'))]
    return out


def width(node, default=0.0):
    """Line width: (stroke (width w)) in KiCad 7+, (width w) before."""
    st = item(node, 'stroke')
    if st is not None and item(st, 'width') is not None:
        return val(st, 'width', default, float)
    return val(node, 'width', default, float)


def filled(node, default=True):
    """Fill flag of a graphic: (fill solid|yes) / (fill none|no); `default` when there is none."""
    f = val(node, 'fill')
    if f is None:
        return default
    return str(f).lower() in ('solid', 'yes', 'true')


def layers(node):
    out = []
    for key in ('layer', 'layers'):
        it = item(node, key)
        if it:
            out += [str(x) for x in it[1:] if isinstance(x, str)]
    return out
