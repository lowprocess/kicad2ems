"""
The steady temperature of a board from what its parts dissipate: a lightweight
layered model (problem.py) solved on a regular grid (grid.py).

    from kicad2ems.pcbqs.thermal import Problem, solve
    r = solve(Problem.of(board, {'Q1': 0.35}), size=0.5)
"""
from .problem import Problem, Result, losses_of, K_CU, K_XY, K_Z, FILM, AMBIENT


def solve(problem, size=None, log=print, **kw):
    """size: the grid's pitch, mm (default 0.5)."""
    from .grid import solve as go
    return go(problem, pitch=size or 0.5, log=log, **kw)
