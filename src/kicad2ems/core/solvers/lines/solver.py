"""
The line model as solvers: qucs (qucsator, Qucs-S's circuit simulator,
run as a program) and scikit (scikit-rf, in process). Both consume the same
network, the transmission-line sections the pipeline builds from the board for
every run (tlines.LineModel), so a difference between the two comes from their
circuit models, not from what they were given (fastsolve.py).
"""
from ...options import Option
from .. import Solver, Result

_CAPS = dict(
    ports='yes, at the network node', gap_ports=
    "part ports only (a floating port in the part's place; no dipole or slot "
    "feeds)",
    diff_pairs='yes', parts='yes',
    vias='approximated: barrels as line sections, vias to ground as '
         'inductances',
    outline='ignored', planes='approximated: as section references only',
    edge_walls='ignored', thickness=
    "yes: each section's copper thickness (the formulas' correction, the "
    "cross-section's rectangles)",
    conductor_loss=
    "approximated: microstrip's by its formula, the other sections' as 2 Rs / "
    "w (strip and return; qucsator: at the band centre)",
    roughness='approximated: its conductivity at the band centre (Hammerstad)',
    finish=
    'approximated: its conductivity at the band centre, by the finished '
    'faces\' share of the loss',
    dispersion=
    "with --diel wideband: each layer's Djordjevic-Sarkar er and tan d, the "
    "network solved at a few of the materials' frequencies and interpolated "
    "between them; else constant er and tan d",
    radiation='no', cavity='no', antenna='no')


def line_sigma(run):
    """The line model's one conductivity: --sigma, with --rough and --finish as
    the loss they give at the band centre (the finish weighted by the ports'
    layer's share of the line's loss). None without --sigma."""
    from kicad2ems.core.roughness import band_sigma
    from kicad2ems.core import finish as F
    sigma = band_sigma(run.s, run.freq[0], run.freq[-1])
    fin = F.resolve(run.s, run.m) if sigma else None
    lays = ([
        p.layer for p in run.m.ports if getattr(p, 'layer', None) in fin.faces
    ] if fin else [])
    if not lays:
        return sigma
    return F.band_sigma(sigma, fin.layers, fin.share(lays[0], line=True),
                        run.freq[0], run.freq[-1], t=fin.thickness(lays[0]))


class LineSolver(Solver):
    consumes, previewed, rank, order = 'network', False, 0, 90
    backend = None                              # fastsolve's backend name
    capabilities = _CAPS

    def assess(self, run):
        if run.s.antenna:
            return ["antenna mode"]
        if run.lm is None:
            return ["the line model could not be built (see the warning above)"]
        if not run.lm.paths:
            return [
                "no routed path from the ports" +
                (f" ({'; '.join(run.lm.notes)})" if run.lm.notes else '')
            ]
        return []

    def solve(self, run, prep):
        from .fastsolve import solve
        from ...sparams import mixed_mode
        wide = getattr(run.s, 'diel', 'fixed') == 'wideband'
        info = {}
        S, seconds = solve(
            run.lm, run.m.ports, run.freq, backend=self.backend,
            sigma=line_sigma(run), qucsator=getattr(run.s, 'qucsator', None),
            wideband=(getattr(run.s, 'diel_fref', 1.0) * 1e9 if wide else None),
            info=info)
        Smm, names, zref = mixed_mode(S, run.m)
        held = info.get('held')
        run.log(f"{self.label}: {seconds * 1e3:.0f} ms" + (
            f" (wideband dielectric: {len(held)} solves with the materials at "
            f"{', '.join(f'{f / 1e9:.3g}' for f in held)} GHz, interpolated "
            "between them)" if held else ''))
        return Result(
            Smm, names, zref, seconds,
            comment=f"line model, {self.backend} ({run.name})", info=dict(
                backend=self.backend, kpi=f"{seconds * 1e3:.0f} ms", about=
                "The transmission-line sections solved as a circuit with "
                f"{self.tool} in {seconds * 1e3:.0f} ms (zero-thickness "
                "copper, like the openEMS model). A ballpark: no coupling "
                "beyond the coupled sections, no radiation, no plane "
                "resonances. Large differences from a full-wave solver point "
                "at something the line model leaves out, or at a problem in "
                "the other model."
            ))


class Qucs(LineSolver):
    name, aliases, label, backend, tool = ('qucs', ('qucsator', ),
                                           'line model (qucsator)', 'qucsator',
                                           'qucsator')
    options = [
        Option(
            'qucsator', None,
            "qucsator executable (default: the one the installer recorded, "
            "else qucsator_rf or qucsator on PATH; the GUI: Solvers and "
            "hardware)", path=True,
            name='path', group='line model', cli=dict(metavar='PATH'))
    ]

    def check(self, s=None):
        from .fastsolve import find_qucsator
        try:
            return True, find_qucsator(getattr(s, 'qucsator', None))
        except ValueError:
            return (False, (
                "no qucsator_rf / qucsator on PATH: install Qucs-S, or give "
                "--qucsator PATH (scikit solves the same line model with "
                "scikit-rf)"))


class Scikit(LineSolver):
    name, aliases, label, backend, tool = ('scikit', ('skrf', 'scikit-rf'),
                                           'line model (scikit-rf)', 'skrf',
                                           'scikit-rf')

    def check(self, s=None):
        try:
            import skrf
        except ImportError:
            return (
                False,
                "scikit-rf is not installed: python -m pip install scikit-rf")
        return True, f"scikit-rf {getattr(skrf, '__version__', '')}".strip()
