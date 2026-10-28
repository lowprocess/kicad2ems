"""
Stand-in solver plugins for kicad2ems's own tests. The full-wave solvers are packages of their own
(kicad2ems-fdtd: openEMS and gfdtd; mom25d); core's tests run against these stand-ins instead, whatever is
installed, so they need none of those packages and see the same solvers on every machine. install() puts them
in place of the installed solvers before kicad2ems.config builds its settings from the registry (conftest.py
calls it as the tests start).

    openems, gfdtd  FDTD-shaped solvers: a few of the shared FDTD settings (the same Option objects on both, in
                    the FDTD groups), gap ports and antenna mode, and a canned answer. openEMS's is usable when
                    CSXCAD and openEMS can be imported (engine_check(), the real ones or fake_openems's), as the
                    real plugin is; it is previewed, as the real one.
    mom25d          a planar solver: it refuses gap ports and boards without a ground plane as mom25d does, and
                    solves to a canned thru. status() and solve() are module functions: replace them in a test.

They keep the real solvers' names, aliases, labels, order and the settings the tests use, so a test reads as a
run of those. What only the real plugins do (the FDTD model, mesh and runs, openEMS's import hints and
--converge, mom25d's mesh and its deviations, gfdtd's backends) is tested in their packages.
"""
import importlib
import numpy as np

from kicad2ems import solvers
from kicad2ems.options import Option, floatlist, items, strlist
from kicad2ems.solvers import Solver, Result, Deviation, AUTO
from kicad2ems.fields.dumps import G_DUMP


# ---- the FDTD solvers ------------------------------------------------------------------------------------
def engine_check():
    """(ok, report lines): can CSXCAD and openEMS be imported? The openEMS stand-in's check (replace it in a test)."""
    ok, lines = True, []
    for mod in ('CSXCAD', 'openEMS'):
        try:
            m = importlib.import_module(mod)
            lines.append(f"  ok      {mod:8s} {getattr(m, '__file__', '')}")
        except Exception as e:                                      # ImportError, or OSError from the libraries
            ok = False
            lines.append(f"  FAILED  {mod:8s} {type(e).__name__}: {e}")
    return ok, lines


def _air(v):
    vals = floatlist(v) if v is not None else []
    if len(vals) not in (0, 1, 6):
        raise ValueError("one value or six (xmin,xmax,ymin,ymax,zmin,zmax)")
    return None if not vals else vals[0] if len(vals) == 1 else tuple(vals)


G_MESH, G_RUN = 'FDTD/Mesh', 'FDTD/Run'                            # the real ones' settings groups and types
MESH = [
    Option('res', 20.0, "cells per wavelength in the dielectric (stand-in)", float, gui='Cells per wavelength',
           group=G_MESH),
    Option('fine', None, "fine cell at copper edges, mm (stand-in)", float, gui='Fine cell (mm)', group=G_MESH),
    Option('air', None, "air margin, mm: one, or six (xmin,xmax,ymin,ymax,zmin,zmax) (stand-in)", str,
           conv=lambda v: _air(v), gui='Air margin, mm (1 or 6 values)', group=G_MESH),
    Option('curves', 'extremes', "mesh lines on curves (stand-in)", choices=('extremes', 'vertices'), group=G_MESH),
]
RUN = [
    Option('bc', 'auto', "boundary conditions (stand-in)", conv=lambda v: ",".join(strlist(v) or ['auto']),
           group=G_RUN),
    Option('end_db', -40.0, "end when the energy is this far down, dB (stand-in)", float, gui='Stop at (dB)',
           group=G_RUN),
    Option('max_steps', 1000000, "timestep limit (stand-in)", int, gui='Timestep limit', group=G_RUN),
    Option('excite', None, "the ports to excite (stand-in; default: all)", list, gui='Driven ports (empty: all)',
           group='solvers'),
    Option('jobs', 1, "runs at once (stand-in)", int, group=G_RUN),
    Option('dump', [], "field dumps FIELD@REGION[@GHz] (stand-in)", list, conv=items, gui='Dumps', group=G_DUMP,
           cli=dict(action='append', metavar='FIELD@REGION[@GHz,...]')),
    Option('dump_port', None, "runs that record dumps (stand-in)", list,
           gui='Runs that record dumps (empty: first driven)', group='field dumps'),
    Option('ff_freq', [], "far-field frequencies, GHz (stand-in)", list, conv=floatlist,
           gui='Antenna: pattern frequencies (GHz; empty: the resonance)', group='antenna', cli=dict(metavar='GHz,...')),
]


def thru(m, freq):
    """A matched, lossless thru between the board's first two ports, 100 ps long; the others open."""
    n = len(m.ports)
    S = np.zeros((len(freq), n, n), complex)
    if n >= 2:
        S[:, 1, 0] = S[:, 0, 1] = np.exp(-2j * np.pi * np.asarray(freq) * 100e-12)
    for k in range(2, n):
        S[:, k, k] = 1
    return S


class FDTD(Solver):
    api = (2, 0)
    rank, previewed, tag, conductor_loss = 2, False, '', True
    capabilities = dict(ports='yes', gap_ports='yes', diff_pairs='yes', parts='yes', vias='yes', outline='yes',
                        planes='yes', edge_walls='yes', thickness='ignored: sheets', conductor_loss='yes',
                        dispersion='yes', radiation='yes', cavity='yes', antenna='yes')
    options = MESH + RUN

    def estimate(self, run):
        return dict(seconds=100.0, gb=0.1, what='0.4 M cells')

    def solve(self, run, prep):
        names = [p.name for p in run.m.ports]
        return Result(thru(run.m, run.freq), names, [p.z0 for p in run.m.ports], 0.1,
                      info=dict(kpi='0.4 M cells', about=f'{self.name} stand-in: a canned answer'),
                      comment=f"{self.name} stand-in ({run.name})")


class OpenEMS(FDTD):
    name, aliases, label = 'openems', ('fdtd',), 'openEMS (FDTD)'
    previewed, order = True, 10

    def summary(self, run):
        return run.summary                                          # as openEMS's: the top level

    def prepare(self, run):
        """As openEMS's: its preview figure and its grid for the 3D view, here a uniform 1 mm grid over the board
        (no mesh rules), so a --preview has both."""
        from kicad2ems import meshes
        from kicad2ems.plots import preview
        x0, y0, x1, y1 = run.m.outline.bounds
        g = dict(x=np.arange(x0, x1 + 1, 1.0), y=np.arange(y0, y1 + 1, 1.0), z=np.array([0.0, run.m.thickness]))
        run.wrote("openEMS grid (3D view)", meshes.save(run, 'openems', 'grid', **g))
        run.figure(preview(run.m, None, run.name), '_mesh.png', 'mesh', 'model and mesh')
        run.log(f"mesh: stand-in, {len(g['x'])} x {len(g['y'])} x {len(g['z'])} lines; wrote {run.path('_mesh.png')}")

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
                        antenna='yes (a phased drive: a run per driven port)')
    options = MESH + RUN + [
        Option('gfdtd_backend', 'auto', "gfdtd: where it runs (stand-in)", choices=('auto', 'gpu', 'cpu'),
               name='backend', gui='Runs on', group='gfdtd'),
    ]

    def check(self, s=None):
        return True, 'gfdtd (stand-in)'

    def assess(self, run):
        """As gfdtd's: for auto, the runs it cannot do are openEMS's."""
        if AUTO not in run.plan:
            return []
        s = run.s
        return [f"{what}: openEMS's" for what, on in (("--sigma (gfdtd's copper is PEC)", s.sigma),) if on]

    def estimate(self, run):
        """openEMS's estimate, twice as long: auto keeps openEMS unless a test says otherwise."""
        return dict(super().estimate(run), seconds=200.0)


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
    api = (2, 0)
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
    """The stand-ins in place of the installed solver packages: before kicad2ems.config (and the GUI's runner)
    build their settings and forms from the registry. Leaves the registry to be built again."""
    solvers._entry_points = entry_points
    solvers._registry, solvers.broken[:] = None, []
