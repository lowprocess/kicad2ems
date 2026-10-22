"""
gfdtd as a solver plugin: the openEMS model, mesh and runs, solved by gfdtd's FDTD engine (GPU via
CuPy, or numpy on the CPU) instead of openEMS's. build_fdtd builds the same CSX model through gfdtd's CSXCAD /
openEMS interface, so the board, the mesh (openEMS's settings: --res, --fine, --nz, ...), the ports, the parts and
the end criteria are openEMS's; only the engine differs.

    prepare  boundaries, the mesh and the dumps, as openEMS
    solve    one gfdtd run per driven port, in <out>/gfdtd/exc_<port> (with the dumps: --dump, --dump-port)
    outputs  the CSX file (openEMS's XML, also runnable with `python -m gfdtd`), field maps, per-run times; in
             antenna mode the far field (gfdtd's NF2FF): <name>_gfdtd_antenna.json / .png, as mom25d's

Not yet: the --converge levels, conductor loss (conducting sheets are PEC), phased drives (--drive).
"""
import os
from types import SimpleNamespace

from . import Result, Option, AUTO
from .openems.plugin import OpenEMS, mesh_zone
from .openems.mesh import air_text

G = 'gfdtd (GPU FDTD)'
# gfdtd's speed over openEMS's estimate (24 threads), per backend: the examples' runs on an RX 7800 XT and an
# i7-13700K, 8-70x on the GPU and 2-2.3x on the core (docs/gfdtd.md#speed); numpy ~10x slower than openEMS.
# START: seconds of setup a run (CuPy's kernels, the core's threads)
SPEEDUP, START = dict(cupy=8.0, core=2.0, numpy=0.1), 2.0


class GFDTD(OpenEMS):
    name, aliases, label = 'gfdtd', ('gpu-fdtd',), 'gfdtd (GPU FDTD)'
    in_auto = True                  # auto: picked over openEMS where it can (no --sigma, --converge, --drive)
    in_all = False                  # --solvers all: openEMS's model again, named on its own
    previewed = False
    capabilities = dict(OpenEMS.capabilities,
                        conductor_loss='ignored: copper is PEC (conducting sheets as PEC)',
                        dispersion='yes: fitted at mid-band, or wideband Debye',
                        antenna='yes: one driven port (no phased drive)')
    options = [
        Option('gfdtd_backend', 'auto', "gfdtd: where it runs - auto (the GPU when CuPy finds one, else the CPU), gpu "
               "(CuPy: CUDA or ROCm) or cpu (the compiled core: python -m gfdtd.core.build; else numpy, slow); its "
               "mesh and run settings are openEMS's (--res, --end-db, ...)", choices=('auto', 'gpu', 'cpu'), name='backend', gui='Runs on', group=G),
    ]

    def check(self, s=None):
        from gfdtd.engine import gpu_info, core_info
        gpu, what = gpu_info()
        want = getattr(s, 'gfdtd_backend', 'auto') if s is not None else 'auto'
        if want == 'gpu' and not gpu:
            return False, f"gfdtd_backend = gpu: {what}"
        if gpu and want != 'cpu':
            return True, f"GPU: {what}"
        core, cwhat = core_info()
        return True, f"CPU: {cwhat if core else 'numpy (slow; build the core: python -m gfdtd.core.build)'}" + (
            '' if gpu else f"; {what}")

    def details(self):
        ok, what = self.check()
        import gfdtd
        return ok, [f"gfdtd {gfdtd.__version__} ({os.path.dirname(gfdtd.__file__)})", what]

    def validate(self, s):
        return None

    def assess(self, run):
        """Named, gfdtd runs without what it lacks (with a note, prepare); for auto, those runs are openEMS's."""
        if AUTO not in run.plan:
            return []
        s = run.s
        return [f"{what}: openEMS's" for what, on in (("--sigma (gfdtd's copper is PEC)", s.sigma),
                                                    ("--converge", s.converge), ("--drive", s.drive)) if on]

    def estimate(self, run):
        """openEMS's estimate (the same mesh and timesteps), at gfdtd's speed on the backend it would use."""
        from gfdtd.engine import resolve_backend
        e = OpenEMS.estimate(self, run)
        where = resolve_backend(self._engine(run.s).partition(':')[2])
        return dict(e, seconds=e['seconds'] / SPEEDUP[where] + START,
                    what=e['what'] + {'cupy': ' on the GPU', 'core': ' on the CPU core'}.get(where, ' in numpy'))

    def _jobs(self, s, p):
        """Runs at once: --jobs when given; on the GPU as many as its cache holds (engine.concurrent_runs), on the
        CPU one (the core's threads already share the cores)."""
        from gfdtd.engine import concurrent_runs, resolve_backend
        if s.jobs > 1:
            return s.jobs
        if resolve_backend(self._engine(s).partition(':')[2]) != 'cupy':
            return 1
        return concurrent_runs(p.grid['info']['cells'], len(p.excite))

    def _engine(self, s):
        return 'gfdtd:' + {'gpu': 'cupy'}.get(s.gfdtd_backend, s.gfdtd_backend)       # cpu: the core, else numpy

    def prepare(self, run):
        from .openems.boundaries import resolve_bc
        from .openems.mesh import auto_mesh
        from .openems.simulation import pulse_steps
        from .openems.dumps import parse_dump
        from .openems.antenna import exc_fmax, antenna_air
        s, m = run.s, run.m
        p = SimpleNamespace(levels=None, coarse=[], dumps=[], result=None, excite=None, ant=None)
        air, bc = s.air, s.bc
        if s.antenna:                                               # as openEMS's: PML, lambda / 4 of air
            air, bc = antenna_air(run.fmin, run.fmax, s.air), ('PML_8' if bc == 'auto' else bc)
            run.log(f"gfdtd antenna mode: boundaries {bc}, air margin {air_text(air)}")
        p.bc = resolve_bc(bc, m)
        p.grid = auto_mesh(m, run.fmin, run.fmax, res=s.res, fine=s.fine, air=air, ratio=s.ratio, nz_min=s.nz,
                           bc=p.bc, curves=s.curves, vias=s.vias, via_snap=s.via_snap, z_sheet=s.z_sheet,
                           curve_res=s.curve_res, zone=mesh_zone(s), log=lambda *a: None)
        if m.walls:
            from ..edges import snap
            p.grid['walls'] = snap(m.walls, p.grid)
        for what, on in (("--converge", s.converge), ("--sigma (copper is PEC)", s.sigma)):
            if on:
                run.log(f"gfdtd: {what} not supported yet - ignored")
        p.dumps = [parse_dump(sp, m, p.grid) for sp in s.dump]
        for d in p.dumps:
            run.log("  " + d.describe(p.grid))
        if s.diel == 'wideband':
            from .openems.dielectric import describe as describe_diel
            run.log("\n".join(describe_diel(m.layers, s.diel_fref * 1e9, run.fmin, run.fmax)))
        ps = pulse_steps(run.fmin, run.fmax, p.grid['info']['dt_ps'],
                         exc_fmax(run.fmin, run.fmax) if s.antenna else None)
        run.summary['gfdtd'] = dict(mesh=p.grid['info'], pulse_steps=ps, backend=s.gfdtd_backend)
        info = p.grid['info']
        run.log(f"gfdtd: {info['cells'] / 1e6:.2f} M cells, boundaries {','.join(p.bc)}, {self.check(s)[1]}")
        return p

    def solve(self, run, p):
        from .openems.simulation import run_excitations, port_index
        from ..sparams import mixed_mode, fill_reciprocal
        s, m = run.s, run.m
        p.excite = port_index(m, s.excite)
        progress = dict(mode='raw' if s.openems_output else 'bar', on_status=run.status if run.events else None)
        p.xml = run.path('_gfdtd.xml')
        dump_runs = port_index(m, s.dump_port) if s.dump_port else None
        run_kw = dict(end_db=s.end_db, end_on=s.end_on, sigma=None, max_steps=s.max_steps, progress=progress,
                      threads=s.threads, log=run.log, xml_path=p.xml, dumps=p.dumps, diel=s.diel,
                      diel_fref=s.diel_fref * 1e9, engine=self._engine(s))
        if s.antenna:
            from .openems import antenna
            if s.drive:
                first = s.drive[0].partition('@')[0].partition('=')[0].strip()
                p.excite = port_index(m, [first])
                run.log(f"note: gfdtd has no phased drive (--drive: mom25d): driving {first} alone")
            elif len(p.excite) > 1:
                run.log(f"gfdtd antenna mode: driving {m.ports[p.excite[0]].name} only (--excite to choose)")
                p.excite = p.excite[:1]
            k = p.excite[0]
            p.result = r = antenna.run_antenna(m, p.grid, run.freq, k, p.bc, s.out,
                                               ff_extra=[v * 1e9 for v in s.ff_freq],
                                               ff_pattern=s.ff_freq[0] * 1e9 if s.ff_freq else None, step=s.ff_step,
                                               **run_kw)
            p.ant = r.extra.get(m.ports[k].name)
        else:
            p.result = r = run_excitations(m, p.grid, run.freq, p.excite, p.bc, s.out, dump_runs=dump_runs,
                                           jobs=self._jobs(s, p), **run_kw)
        missing = fill_reciprocal(r.S, p.excite)
        if missing:
            run.log(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the "
                    f"terms between unexcited ports are unknown (written as 0)")
        S, names, zref = mixed_mode(r.S, m)
        seconds = sum(v or 0 for v in r.seconds.values())
        cells = p.grid['info']['cells']
        steps = sum(v or 0 for v in r.steps.values())
        return Result(S, names, zref, seconds, comment=f"gfdtd FDTD ({run.name})",
                      info=dict(excited=[m.ports[k].name for k in p.excite], kpi=f"{seconds:.0f} s solver",
                                about=f"gfdtd, 3D FDTD ({s.gfdtd_backend}): {len(r.seconds)} run(s) on "
                                      f"{cells / 1e6:.2f} M cells, ~{steps:,} timesteps, {seconds:.0f} s "
                                      f"({cells * steps / max(seconds, 1e-9) / 1e6:.0f} M cell updates/s)."))

    def outputs(self, run, p, res):
        from .openems.simulation import run_dir, log_path
        s, m, r = run.s, run.m, p.result
        run.wrote('CSX geometry for gfdtd (openEMS XML)', p.xml)
        self._dump_outputs(run, p, prefix='gfdtd ')
        runs = {n: dict(seconds=r.seconds.get(n), steps=r.steps.get(n), ended=r.ended.get(n)) for n in r.seconds}
        for k in p.excite:
            runs.setdefault(m.ports[k].name, {})['log'] = run.rel(log_path(run_dir(m, k, s.out, 'gfdtd')))
        run.summary.setdefault('gfdtd', {})['runs'] = runs
        if p.ant:
            self._antenna_gfdtd(run, p)

    def _antenna_gfdtd(self, run, p):
        """The antenna results, as mom25d writes its own: <name>_gfdtd_antenna.json (solver, sphere_file) and plots."""
        from .openems.antenna import save as save_antenna
        from .openems.simulation import run_dir
        from ..plots import plot_antenna, plot_pattern
        k, m = p.excite[0], run.m
        sph = os.path.join(run_dir(m, k, run.s.out, 'gfdtd'), 'nf2ff_sphere.h5')
        ant = dict(p.ant, solver='gfdtd', sphere_file=os.path.relpath(sph, run.s.out))
        tag = '' if 'antenna' not in run.summary else '_gfdtd'           # openEMS's antenna results come first
        run.figure(plot_antenna(run.freq, p.result.S[:, k, k], m.ports[k].z0, ant, f"{run.name} - antenna (gfdtd)"),
                   '_gfdtd_antenna.png', f"antenna{tag}", 'antenna (gfdtd): match, gain, efficiency')
        run.figure(plot_pattern(ant, f"{run.name} - gain pattern (gfdtd)"), '_gfdtd_pattern.png', f"pattern{tag}",
                   'antenna (gfdtd): patterns')
        js = run.path('_gfdtd_antenna.json')
        save_antenna(ant, js)
        run.wrote('antenna results, gfdtd (JSON)', js)
        run.wrote('antenna sphere, gfdtd', sph)
        run.summary['antenna' + tag] = {k_: v for k_, v in ant.items() if k_ != 'sphere'}
