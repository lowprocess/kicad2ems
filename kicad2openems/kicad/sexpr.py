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


def layers(node):
    out = []
    for key in ('layer', 'layers'):
        it = item(node, key)
        if it:
            out += [str(x) for x in it[1:] if isinstance(x, str)]
    return out
