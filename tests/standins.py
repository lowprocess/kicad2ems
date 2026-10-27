"""
Stand-in solver plugins for kicad2ems's own tests. The full-wave solvers are packages of their own
(kicad2ems-openems, mom25d, gfdtd); core's tests run against these stand-ins instead, whatever is installed, so
they need none of those packages and see the same solvers on every machine. install() puts them in place of the
installed solvers and FDTD engines before kicad2ems.config builds its settings from the registry (conftest.py
calls it as the tests start).

    openems, gfdtd  the FDTD plugin class (kicad2ems.fdtd.plugin, the real code) on a stand-in engine: CSXCAD and
                    openEMS as Python imports them (the real ones, or fake_openems's), so a model is built and a
                    run needs them as with openEMS's plugin. engine_check() is its import check.
    mom25d          a planar solver: it refuses gap ports and boards without a ground plane as mom25d does, and
                    solves to a canned thru. status() and solve() are module functions: replace them in a test.

They keep the real solvers' names, aliases, labels, order and the settings the tests use, so a test reads as a
run of those. What only the real plugins do (openEMS's import hints and --converge, mom25d's mesh and its
deviations, gfdtd's backends) is tested in their packages.
"""
import importlib, types
import numpy as np

from kicad2ems import solvers
from kicad2ems.fdtd import engines
from kicad2ems.fdtd.plugin import FDTD, MESH, RUN
from kicad2ems.options import Option, floatlist
from kicad2ems.solvers import Solver, Result, Deviation, AUTO


# ---- the FDTD engine: CSXCAD / openEMS as imported -------------------------------------------------------
def engine_api(_option=''):
    """(ContinuousStructure, openEMS) from the CSXCAD and openEMS modules, the structure given AddDebyeMaterial
    where it lacks it (as openEMS's plugin does)."""
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS
    if hasattr(ContinuousStructure, 'AddDebyeMaterial'):
        return ContinuousStructure, openEMS

    class Structure(ContinuousStructure):
        def AddDebyeMaterial(self, name, **kw):
            from CSXCAD.CSProperties import CSPropDebyeMaterial
            mat = CSPropDebyeMaterial(self.GetParameterSet(), name=name, **kw)
            self.AddProperty(mat)
            return mat
    return Structure, openEMS


def engine_check():
    """(ok, report lines): can CSXCAD and openEMS be imported? The FDTD stand-ins' check (replace it in a test)."""
    ok, lines = True, []
    for mod in ('CSXCAD', 'openEMS'):
        try:
            m = importlib.import_module(mod)
            lines.append(f"  ok      {mod:8s} {getattr(m, '__file__', '')}")
        except Exception as e:                                      # ImportError, or OSError from the libraries
            ok = False
            lines.append(f"  FAILED  {mod:8s} {type(e).__name__}: {e}")
    return ok, lines


ENGINES = {'openems': types.SimpleNamespace(api=engine_api, LABEL='openEMS'),
           'gfdtd': types.SimpleNamespace(api=engine_api, LABEL='gfdtd')}


class OpenEMS(FDTD):
    name, aliases, label = 'openems', ('fdtd',), 'openEMS (FDTD)'
    previewed, order = True, 10

    def engine(self, s):
        return 'openems'

    def summary(self, run):
        return run.summary                                          # as openEMS's: the top level

    def check(self, s=None):
        ok, lines = engine_check()
        failed = [l.split(None, 1)[1] for l in lines if l.strip().startswith('FAILED')]
        return (True, 'openEMS (stand-in)') if ok else (False, failed[0] if failed else 'openEMS cannot be imported')

    def details(self):
        return engine_check()


class GFDTD(FDTD):
    name, aliases, label = 'gfdtd', ('gpu-fdtd',), 'gfdtd (GPU FDTD)'
    in_all, order, tag, conductor_loss = False, 40, '_gfdtd', False
    capabilities = dict(FDTD.capabilities, conductor_loss='ignored: copper is PEC (conducting sheets as PEC)',
                        antenna='yes: one driven port (no phased drive)')
    options = MESH + RUN + [
        Option('gfdtd_backend', 'auto', "gfdtd: where it runs (stand-in)", choices=('auto', 'gpu', 'cpu'),
               name='backend', gui='Runs on', group='gfdtd'),
    ]

    def engine(self, s):
        return 'gfdtd'

    def check(self, s=None):
        return True, 'gfdtd (stand-in)'

    def assess(self, run):
        """As gfdtd's: for auto, the runs it cannot do are openEMS's."""
        if AUTO not in run.plan:
            return []
        s = run.s
        return [f"{what}: openEMS's" for what, on in (("--sigma (gfdtd's copper is PEC)", s.sigma),
                                                    ("--drive", s.drive)) if on]

    def estimate(self, run):
        """openEMS's estimate, twice as long: auto keeps openEMS unless a test says otherwise."""
        e = super().estimate(run)
        return dict(e, seconds=2 * e['seconds'])


# ---- the planar solver ------------------------------------------------------------------------------------
def status():
    """(usable, what) of the planar stand-in (replace it in a test)."""
    return True, 'mom25d (stand-in)'


def solve(m, freq, **kw):
    """A canned answer over the board's ports: a matched, lossless thru between the first two, 100 ps long; the
    others open. Returns (S, names, zref, info) as mom25d's solve does."""
    n = len(m.ports)
    S = np.zeros((len(freq), n, n), complex)
    if n >= 2:
        S[:, 1, 0] = S[:, 0, 1] = np.exp(-2j * np.pi * np.asarray(freq) * 100e-12)
    for k in range(2, n):
        S[:, k, k] = 1
    info = dict(seconds=0.1, solves=1, unknowns=0, triangles=0, solver='stand-in', dmax_mm=kw.get('dmax', 0.3),
                ports=[dict(name=p.name, kind='lumped', zc=0.0) for p in m.ports], notes=[])
    return S, [p.name for p in m.ports], [50.0] * n, info


class MoM25D(Solver):
    name, aliases, label = 'mom25d', ('mom',), 'mom25d (MoM)'
    consumes, rank, order = 'board', 1, 20
    capabilities = dict(ports='yes', gap_ports='no', diff_pairs='yes', parts='yes', vias='yes',
                        outline='ignored: laterally infinite dielectric', planes='yes',
                        edge_walls='approximated: via rows', thickness='ignored: sheets', conductor_loss='yes',
                        dispersion='no: constant tan d', radiation='yes', cavity='no',
                        antenna='approximated: over an infinite ground plane')
    options = [
        Option('mom_dmax', 0.3, "largest MoM cell along the copper, mm (stand-in)", float, name='dmax',
               gui='Largest cell along the copper (mm)', group='mom25d', cli=dict(metavar='MM')),
        Option('mom_port', 'auto', "ports (stand-in)", choices=('auto', 'wave', 'lumped'), name='port', gui='Ports',
               group='mom25d'),
        Option('mom_solves', 60, "most EM solves of the sweep (stand-in)", int, name='solves',
               gui='Most EM solves of the sweep', group='mom25d', cli=dict(metavar='N')),
        Option('mom_dump', [], "current dumps (GHz, stand-in)", list, conv=floatlist, name='dump',
               gui='Current dumps (GHz, comma-separated)', group='mom25d', cli=dict(metavar='GHz,...')),
    ]

    def check(self, s=None):
        return status()

    def assess(self, run):
        """mom25d's rules: no gap ports, and a solid ground plane."""
        m, out = run.m, []
        gaps = [p.name for p in m.ports if p.kind == 'gap']
        if gaps:
            out.append(f"gap port(s) {', '.join(gaps)}: no planar equivalent")
        half = 0.5 * m.outline.area
        if not m.ground and not any(sum(p.area for p in m.copper.get(l, [])) >= half for l in m.cu_order):
            out.append("no solid ground plane (no copper layer covers half the board): set ground = <layer>")
        return out

    def deviations(self, run, prep):
        return [Deviation('outline', 'outline', 'left out: laterally infinite dielectric', dropped=True)]

    def estimate(self, run):
        return dict(seconds=60.0, gb=0.1, what='stand-in')

    def solve(self, run, prep):
        S, names, zref, info = solve(run.m, run.freq, dmax=run.s.mom_dmax, port=run.s.mom_port, log=run.log)
        info.update(kpi=f"{info['unknowns']:,} unknowns", about='mom25d stand-in: a canned answer')
        return Result(S, names, zref, info['seconds'], info=info, comment=f"mom25d stand-in ({run.name})")


# ---- in place of the installed packages -------------------------------------------------------------------
class _EntryPoint:
    def __init__(self, name, obj):
        self.name, self.value, self._obj = name, f"standins:{name}", obj

    def load(self):
        return self._obj


SOLVERS = [OpenEMS, MoM25D, GFDTD]


def entry_points():
    """The stand-ins as the 'kicad2ems.solvers' entry points."""
    return [_EntryPoint(c.name, c) for c in SOLVERS]


def install():
    """The stand-ins in place of the installed solver packages and FDTD engines: before kicad2ems.config (and the
    GUI's runner) build their settings and forms from the registry. Leaves the registry to be built again."""
    solvers._entry_points = entry_points
    solvers._registry, solvers.broken[:] = None, []
    engines._entry_points = lambda: [_EntryPoint(n, e) for n, e in ENGINES.items()]
