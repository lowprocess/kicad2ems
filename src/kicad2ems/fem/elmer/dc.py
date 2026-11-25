"""DC IR drop of a net's copper with Elmer: steady current conduction in the 3D
copper and via barrels.

    python -m kicad2ems.fem.elmer.dc board.kicad_pcb --net VHB --source L3 \\
        --load Q1=0.67 --load Q2=0.67 --out dc_vhb

The source part's pads on the net are held at 0 V; each load draws its current
through its pads on the net as one equipotential terminal. The copper is the
layered mesh the thermal tool solves on (layered.py: the board's triangles swept
through the stack as wedges, the net's copper and via barrels its bodies;
R-D23). Several --net are one conductor (a rail
through 0 ohm links joined copper); series parts (sense resistors, beads) are
not modelled: pcbqs.pdn does rail trees and returns, this is the 3D check of
their copper. Writes <out>/dc.json (each load's voltage, the copper's loss, the
mesh) and Elmer's case in <out>/elmer.

    from kicad2ems.fem.elmer.dc import solve
    r = solve(board, {'VHB'}, 'L3', {'Q1': 0.67}, out='dc_vhb')
        # r['V']['Q1'] (V, negative: the drop), r['loss_W']
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from ..geometry import load_board, pads_of
from . import run as elmer
from .layered import SIGMA_CU

PLATING = 0.025


def _solvers(masks, bcs):
    """The .sif's solvers, the current's (direct) and the loads' mean
    potentials saved, and the boundary conditions."""
    return f"""Solver 1
  Equation = "StatCurrent"
  Variable = "Potential"
  Procedure = "StatCurrentSolveVec" "StatCurrentSolver"
  Linear System Solver = "Direct"
  Linear System Direct Method = "Umfpack"
End
Solver 2
  Exec Solver = After All
  Equation = "SaveScalars"
  Procedure = "SaveData" "SaveScalars"
  Filename = "scalars.dat"
{''.join(masks)}End
{''.join(bcs)}"""


def layered_sif(m, loads, plating=PLATING):
    """The .sif on the layered mesh (layered_mesh's description, boundaries
    named 'I:source' and 'I:<load>'): the current in the net's copper and
    barrels, the other bodies left out of the solve."""
    from .layered import COPPER_E, BARREL_E
    r = m['via_r']
    # (a mesh with each barrel its plating's cylinder: copper as it is)
    fill = (1.0 if m.get('plating') or not len(r) else float(np.mean(
        (r**2 - np.maximum(r - plating, 0)**2) / r**2)))
    bodies = ''
    for i, b in enumerate(m['bodies']):
        on = b in (COPPER_E, BARREL_E)
        bodies += (
            f"Body {i + 1}\n  Target Bodies(1) = {b}\n"
            f"  Equation = {1 if on else 2}\n"
            "  Material = "
            f"{1 if b == COPPER_E else 2 if b == BARREL_E else 3}\nEnd\n")
    bcs, masks, k = [], [], 0
    for i, (tag, (names, _)) in enumerate(m['boundaries'].items()):
        lines = []
        for n in names:
            if n == 'I:source':
                lines.append("  Potential = 0.0")
            elif n.startswith('I:') and n[2:] in loads:
                lines.append(
                    "  Potential Constant = Logical True\n"
                    "  Potential Constant Flux = Real "
                    f"{-loads[n[2:]]:.12g}\n"
                    f"  load{k} = Logical True")
                masks.append(
                    f"  Variable {k + 1} = Potential\n"
                    f"  Operator {k + 1} = boundary mean\n"
                    f"  Mask Name {k + 1} = load{k}\n")
                k += 1
        if lines:
            bcs.append(
                f"Boundary Condition {len(bcs) + 1}\n"
                f"  Target Boundaries(1) = {tag}\n"
                + '\n'.join(lines) + "\nEnd\n")
    return elmer.header() + f"""{bodies}Equation 1
  Active Solvers(1) = 1
End
Equation 2
  Active Solvers(1) = 2
End
Material 1
  Electric Conductivity = {SIGMA_CU:.6g}
End
Material 2
  Electric Conductivity = {SIGMA_CU * fill:.6g}
End
Material 3
  Electric Conductivity = 0.0
End
{_solvers(masks, bcs)}"""


def solve(board, nets, source, loads, out='dc', h=0.25, plating=PLATING,
          log=print):
    """Each load's voltage (V, relative to the source: negative, the drop) and
    the copper's loss (W), on the layered mesh (R-D23)."""
    elmer.require()
    if not loads or any(a <= 0 for a in loads.values()):
        raise ValueError('loads: one or more, each a positive current')
    out = Path(out)
    case = out / 'elmer'
    case.mkdir(parents=True, exist_ok=True)
    nets = {nets} if isinstance(nets, str) else set(nets)
    terms = {
        'source': pads_of(board, source, nets),
        **{ref: pads_of(board, ref, nets)
           for ref in loads}
    }
    from shapely.geometry import box, mapping
    from ..geometry import copper_of, layers as layer_names
    from .layered import layered_mesh
    t = time.perf_counter()
    cu = [
        g for l in layer_names(board) for g in [copper_of(board, nets, l)]
        if g is not None and not g.is_empty
    ]
    if not cu:
        raise ValueError(f"no copper of {', '.join(sorted(nets))}")
    x0, y0, x1, y1 = np.array([g.bounds for g in cu]).T
    # the net's copper and 1 mm round it
    crop = box(x0.min() - 1, y0.min() - 1, x1.max() + 1, y1.max() + 1)
    if not board.get('outline'):
        board = dict(board, outline=mapping(crop))
    m = layered_mesh(board, case / 'mesh', electric=nets, copper=set(),
                     terminals={f'I:{k}': v
                                for k, v in terms.items()}, crop=crop, h=h,
                     plating=plating, log=log)
    mesh_s = time.perf_counter() - t
    t = time.perf_counter()
    said = elmer.solve(case, layered_sif(m, loads, plating))
    s = elmer.scalars(case / 'scalars.dat')
    V = {
        ref: float(
            next(v
                 for c, v in s.items()
                 if 'potential' in c.lower() and f'load{k}' in c.lower()))
        for k, ref in enumerate(loads)
    }
    # the power the loads draw through the copper. Not Elmer's Joule heating
    # field: the loop that fills it is not safe with several threads
    # (StatCurrentSolver_post), and now and then gives another figure
    loss = float(sum(-V[ref] * amps for ref, amps in loads.items()))
    result = dict(
        format=1, tool='kicad2ems.fem.elmer.dc', engine=elmer.engine(said),
        nets=sorted(nets), source=source, loads=dict(loads), V=V,
        drop_mV={r: -v * 1e3
                 for r, v in V.items()}, loss_W=loss, mesh='wedge',
        wedges=m['wedges'], triangles=m['triangles'], vias=m['vias'], h=h,
        seconds=dict(mesh=round(mesh_s, 1), solve=round(time.perf_counter() - t,
                                                        1)))
    (out / 'dc.json').write_text(json.dumps(result, indent=1))
    return result


def parser():
    ap = argparse.ArgumentParser(prog='python -m kicad2ems.fem.elmer.dc',
                                 description=__doc__.split('\n\n')[0])
    ap.add_argument('board',
                    help='.kicad_pcb, or a kicad2ems <name>_board.json')
    ap.add_argument('--net', action='append', required=True,
                    help='the net (repeat: nets joined as one conductor)')
    ap.add_argument('--source', required=True,
                    help="the part whose pads on the net are held at 0 V")
    ap.add_argument('--load', action='append', required=True,
                    help='REF=AMPS (repeat)')
    ap.add_argument('--out', default='dc', help='output folder')
    ap.add_argument('--h', type=float, default=0.25,
                    help='mesh size in the copper, mm (default 0.25)')
    ap.add_argument('--plating', type=float, default=PLATING,
                    help='via plating, mm (default 0.025)')
    return ap


def main(argv=None):
    from .. import children
    children.guard()  # its solvers stopped with it
    ap = parser()
    a = ap.parse_args(argv)
    loads = {}
    for item in a.load:
        ref, _, amps = item.partition('=')
        try:
            loads[ref.strip()] = float(amps)
        except ValueError:
            ap.error(f'--load {item!r}: expected REF=AMPS')
    try:
        board = load_board(a.board, set(a.net))
        r = solve(board, set(a.net), a.source, loads, out=a.out, h=a.h,
                  plating=a.plating,
                  log=lambda s: print('fem dc:', s, flush=True))
    except (ValueError, RuntimeError) as exc:
        print(f'fem dc: error: {exc}', file=sys.stderr)
        return 2
    for ref, mv in r['drop_mV'].items():
        print(f'fem dc: {ref}: {mv:.4f} mV')
    print(
        f"fem dc: {r['loss_W'] * 1e3:.4f} mW in the copper; "
        f"{r['wedges']:,} wedges; wrote "
        f"{Path(a.out) / 'dc.json'}"
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
