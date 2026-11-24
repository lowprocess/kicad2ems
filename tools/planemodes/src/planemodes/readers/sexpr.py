"""A small S-expression parser for KiCad files.

parse(text) gives nested lists of strings: (a "b c" (d 1)) -> ['a', 'b c',
['d', '1']]. Quoted strings lose their quotes; numbers stay strings (callers
convert what they need).
"""
import re

_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')


def parse(text):
    """The first top-level expression of `text`."""
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        t = m.group()
        if t == '(':
            stack.append(cur)
            cur = []
        elif t == ')':
            done, cur = cur, stack.pop()
            cur.append(done)
        elif t[0] == '"':
            cur.append(t[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        else:
            cur.append(t)
    return cur[0]


def find(node, key):
    """The first child list of `node` whose head is `key`, or None."""
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def findall(node, key):
    """Every child list of `node` whose head is `key`."""
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def val(node, key, default=None, cast=str):
    """The first value of the child `key` (e.g. (width 0.2) -> 0.2)."""
    c = find(node, key)
    return cast(c[1]) if c is not None and len(c) > 1 else default


def xy(node, key='at'):
    """(x, y) of a child such as (at 1 2) or (start 1 2), else None."""
    c = find(node, key)
    return (float(c[1]), float(c[2])) if c is not None else None
