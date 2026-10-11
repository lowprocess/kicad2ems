"""
The line model as solver plugins: qucs (qucsator, Qucs-S's circuit simulator, run as a program) and scikit
(scikit-rf, in process). Both consume the same network, the transmission-line sections the pipeline builds
from the board for every run (tlines.LineModel), so a difference between the two comes from their circuit
models, not from what they were given (fastsolve.py).
"""
from ...options import Option
from .. import Solver, Result

_CAPS = dict(ports='yes, at the network node', gap_ports='no', diff_pairs='yes', parts='yes',
             vias='approximated: barrels as line sections, vias to ground as inductances',
             outline='ignored', planes='approximated: as section references only', edge_walls='ignored',
             thickness='ignored: zero-thickness sections, as openEMS', conductor_loss='yes',
             dispersion='no: constant tan d', radiation='no', cavity='no', antenna='no')


class LineSolver(Solver):
    consumes, previewed, rank = 'network', False, 0
    backend = None                              # fastsolve's backend name
    capabilities = _CAPS

    def assess(self, run):
        if run.s.antenna:
            return ["antenna mode"]
        if run.lm is None:
            return ["the line model could not be built (see the warning above)"]
        if not run.lm.paths:
            return ["no routed path from the ports" + (f" ({'; '.join(run.lm.notes)})" if run.lm.notes else '')]
        return []

    def solve(self, run, prep):
        from .fastsolve import solve
        from ...sparams import mixed_mode
        S, seconds = solve(run.lm, run.m.ports, run.freq, backend=self.backend, sigma=run.s.sigma,
                           qucsator=getattr(run.s, 'qucsator', None))
        Smm, names, zref = mixed_mode(S, run.m)
        run.log(f"{self.label}: {seconds * 1e3:.0f} ms")
        return Result(Smm, names, zref, seconds, comment=f"line model, {self.backend} ({run.name})",
                      info=dict(backend=self.backend, kpi=f"{seconds * 1e3:.0f} ms",
                                about=f"The transmission-line sections solved as a circuit with {self.tool} in "
                                      f"{seconds * 1e3:.0f} ms (zero-thickness copper, like the openEMS model). A "
                                      f"ballpark: no coupling beyond the coupled sections, no radiation, no plane "
                                      f"resonances. Large differences from a full-wave solver point at something "
                                      f"the line model leaves out, or at a problem in the other model."))


class Qucs(LineSolver):
    name, aliases, label, backend, tool = 'qucs', ('qucsator',), 'line model (qucsator)', 'qucsator', 'qucsator'
    options = [Option('qucsator', None, "qucsator executable (default: qucsator_rf or qucsator on PATH)", path=True,
                      name='path', group='line model', cli=dict(metavar='PATH'))]

    def check(self, s=None):
        from .fastsolve import find_qucsator
        try:
            return True, find_qucsator(getattr(s, 'qucsator', None))
        except ValueError:
            return False, ("no qucsator_rf / qucsator on PATH: install Qucs-S, or give --qucsator PATH (scikit "
                           "solves the same line model with scikit-rf)")


class Scikit(LineSolver):
    name, aliases, label, backend, tool = 'scikit', ('skrf', 'scikit-rf'), 'line model (scikit-rf)', 'skrf', 'scikit-rf'

    def check(self, s=None):
        try:
            import skrf
        except ImportError:
            return False, "scikit-rf is not installed: python -m pip install scikit-rf"
        return True, f"scikit-rf {getattr(skrf, '__version__', '')}".strip()
