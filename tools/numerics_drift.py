#!/usr/bin/env python3
"""How far pcbqs's copies of mom25d's numerics have drifted (R-F4): the modules both packages carry, function by
function. Each package keeps its own (pcbqs reads no mom25d), so a fix made in one does not reach the other by
itself: mom25d's smaller-triangle singular rule (a moment 1.3 % off) had not reached pcbqs, nor pcbqs's mesher
quality fix mom25d. This reports the functions whose code differs (docstrings and comments aside) and those only one
side has, so a fix is ported or the difference is meant.

    python tools/numerics_drift.py            # the report (the checkouts side by side: ../pcbqs, ../mom25d); exit 0
    python tools/numerics_drift.py --strict   # exit 1 when a function both have differs

Known (2026-11-15), each a fix to port with every kernel that has the same rule:
- sing.offplane_pairs: mom25d's 6-point rule over the smaller triangle (rule_over) is not in pcbqs. Ported in
  Python it changes pcbqs's analytic pairs, but pcbqs's compiled static and compressed/layered kernels and its CuPy
  kernel integrate over p too, and its tests hold them to the Python one: all of them together.
- geo.mesh_polygons: pcbqs's minimum-angle `quality` (Triangle's q, with a cap on the points it adds: DC meshes) is
  not in mom25d; off by default, so no answer differs.
Meant: geo._run_grid and sing._offplane_geometry differ only in where they import from (each package's own).
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                         # the checkouts' folder
PAIRS = [('geo', 'pcbqs/src/pcbqs/_numerics/geo.py', 'mom25d/src/mom25d/geo.py'),
         ('base', 'pcbqs/src/pcbqs/_numerics/base.py', 'mom25d/src/mom25d/base.py'),
         ('sing', 'pcbqs/src/pcbqs/_numerics/sing.py', 'mom25d/src/mom25d/sing.py'),
         ('biot', 'pcbqs/src/pcbqs/biot.py', 'mom25d/src/mom25d/biot.py')]


def functions(path):
    """{name: its code without docstrings, comments or blank lines} of a module's top-level functions and classes."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for sub in ast.walk(node):                              # docstrings out
                body = getattr(sub, 'body', None)
                if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                        isinstance(getattr(body[0], 'value', None), ast.Constant) and isinstance(body[0].value.value, str):
                    sub.body = body[1:] or [ast.Pass()]
            out[node.name] = ast.unparse(node)
    return out


def main(argv):
    strict = '--strict' in argv
    differ = 0
    for name, a, b in PAIRS:
        fa, fb = functions(ROOT / a), functions(ROOT / b)
        both = sorted(set(fa) & set(fb))
        changed = [f for f in both if fa[f] != fb[f]]
        only_a, only_b = sorted(set(fa) - set(fb)), sorted(set(fb) - set(fa))
        differ += len(changed)
        print(f"{name}: {len(both)} in both, {len(changed)} differ" + (f": {', '.join(changed)}" if changed else '')
              + (f"; only pcbqs: {', '.join(only_a)}" if only_a else '') + (f"; only mom25d: {', '.join(only_b)}" if only_b else ''))
    return 1 if strict and differ else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
