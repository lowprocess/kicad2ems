"""
One run of kicad2openems as a sequence of stages, driven by the parsed command line / TOML options:

    Job(args).run()
        model()     KiCadBoard -> Model (stack, copper, vias, ports, lumped parts)
        mesh()      boundaries, automatic mesh (or the --converge levels), cavity check, field-dump specs
        preview()   <name>_mesh.png, excitation length; --preview stops here
        solve()     openEMS runs: S-parameters (all excited ports, --jobs), --converge levels, or antenna mode
        outputs()   Touchstone, plots, field-dump maps, antenna / convergence results, <name>_openems.json
        report()    <name>_report.html (report.py), also after --preview

Every message goes through Job.log, which prints it and keeps it, so the report can list the run's
warnings; Job.files collects the files written.
"""
import json, os, time
import numpy as np

from .kicad.board import KiCadBoard
from .model import Model
from .boundaries import resolve_bc, cavity_check
from .mesh import auto_mesh
from .plots import preview, plot_results, plot_convergence, plot_antenna, plot_pattern
from .sparams import mixed_mode, fill_reciprocal
from .touchstone import write_touchstone, read_touchstone
from .dumps import parse_dump, estimate_size_mb, plot_fd_dumps


def ram_gb():
    """Installed RAM in GB (None where sysconf cannot tell)."""
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1e9
    except (ValueError, OSError, AttributeError):
        return None


class Job:
    def __init__(self, args, echo=print):
        self.a, self.echo = args, echo
        self.messages, self.files = [], []
        self.name = os.path.splitext(os.path.basename(args.board))[0]
        self.fmin, self.fmax = args.fmin * 1e9, args.fmax * 1e9
        self.antenna = args.mode == 'antenna'
        self.t0 = time.time()
        self.levels = self.ant = self.result = None
        self.coarse, self.dumps, self.info = [], [], {}

    # ---- helpers -----------------------------------------------------------------------------------
    def log(self, *msg):
        text = " ".join(str(x) for x in msg)
        self.echo(text)
        self.messages.extend(text.split("\n"))

    @property
    def warnings(self):
        return [l.strip() for l in self.messages if 'WARNING' in l or l.strip().lower().startswith('warning')]

    def path(self, suffix):
        return os.path.join(self.a.out, f"{self.name}{suffix}")

    def wrote(self, label, path):
        self.files.append((label, path))

    # ---- stages --------------------------------------------------------------------------------------
    def run(self):
        os.makedirs(self.a.out, exist_ok=True)
        self.model()
        self.mesh()
        self.preview()
        if self.a.preview:
            self.report()
            return 0
        from .simulation import check_openems
        ok, lines = check_openems()
        if not ok:
            self.log("openEMS cannot be imported - details:\n" + "\n".join(lines))
            return 2
        self.solve()
        self.outputs()
        self.report()
        return 0

    def model(self):
        a = self.a
        region = tuple(float(v) for v in a.region.split(',')) if a.region else None
        self.board = KiCadBoard(a.board)
        self.m = Model(self.board, nets=a.nets.split(',') if a.nets else None, pos_file=a.pos, include_mask=a.mask,
                       region=region, ground=a.ground, parts=a.parts, log=self.log)
        self.log(self.m.describe())
        if a.diel == 'wideband':
            from .dielectric import describe as describe_diel
            self.log("\n".join(describe_diel(self.m.layers, a.diel_fref * 1e9, self.fmin, self.fmax)))

    def mesh(self):
        a, m = self.a, self.m
        if self.antenna:
            from .antenna import antenna_defaults
            if a.converge:
                raise SystemExit("kicad2openems: --converge is not available with --mode antenna")
            a.air, _ = antenna_defaults(self.fmin, self.fmax, a.air)
            if a.bc == 'auto':
                a.bc = 'PML_8'
            self.log(f"antenna mode: boundaries {a.bc}, air margin {a.air:.1f} mm")
        self.bc = resolve_bc(a.bc, m)
        kw = dict(air=a.air, ratio=a.ratio, nz_min=a.nz, bc=self.bc, curves=a.curves, vias=a.vias,
                  via_snap=a.via_snap, log=self.log)
        if a.converge:
            from .converge import parse_levels, mesh_levels, describe_levels
            self.levels = mesh_levels(m, self.fmin, self.fmax, parse_levels(a.converge), res=a.res, fine=a.fine, **kw)
            self.log("\n".join(describe_levels(self.levels)))
            self.grid = self.levels[-1][1]                          # the finest level gives the result
        else:
            self.grid = auto_mesh(m, self.fmin, self.fmax, res=a.res, fine=a.fine, **kw)
        cavity_check(m, self.grid, self.bc, self.fmax, log=self.log)
        for sp in (a.dump or []):                                  # validate dump specs before any run
            self.log("  " + parse_dump(sp, m, self.grid).describe(self.grid))

    def preview(self):
        from .simulation import pulse_steps
        from .antenna import exc_fmax
        a = self.a
        png = self.path('_mesh.png')
        preview(self.m, self.grid, png, self.name)
        self.wrote('model and mesh', png)
        self.log(f"wrote {png}")
        ps = pulse_steps(self.fmin, self.fmax, self.grid['info']['dt_ps'],
                         exc_fmax(self.fmin, self.fmax) if self.antenna else None)
        self.pulse = ps
        self.log(f"excitation pulse ~{ps} timesteps (estimate); --max-steps {a.max_steps} allows "
                 f"{a.max_steps / ps:.0f} pulse lengths" +
                 (" - WARNING: few for the fields to ring down: raise --max-steps" if a.max_steps < 20 * ps else ''))

    def solve(self):
        from .simulation import run_excitations, port_index
        a, m = self.a, self.m
        self.excite = port_index(m, a.excite)
        if self.antenna and len(self.excite) > 1:
            self.log(f"antenna mode: driving {m.ports[self.excite[0]].name} only (one port per antenna run; "
                     f"--excite to choose)")
            self.excite = self.excite[:1]
        self.dumps = [parse_dump(sp, m, self.grid) for sp in (a.dump or [])]
        dump_runs = port_index(m, a.dump_port) if a.dump_port else None
        if self.dumps:
            n_runs = len(dump_runs) if dump_runs else 1
            self.log(f"field dumps: ~{estimate_size_mb(self.dumps, self.grid) * n_runs:.0f} MB on disk (rough), in "
                     f"{n_runs} run(s)")
        self.freq = np.linspace(self.fmin, self.fmax, a.points)
        jobs = max(1, min(a.jobs, len(self.excite)))
        ram = ram_gb()
        if jobs > 1 and ram and jobs * self.grid['info']['mem_gb'] > 0.8 * ram:
            self.log(f"WARNING: {jobs} parallel runs need ~{jobs * self.grid['info']['mem_gb']:.1f} GB, "
                     f"{ram:.1f} GB RAM installed - use fewer --jobs")
        progress = dict(mode='raw' if a.openems_output else 'bar')
        run_kw = dict(end_db=a.end_db, sigma=a.sigma, threads=a.threads, jobs=jobs, diel=a.diel,
                      max_steps=a.max_steps, diel_fref=a.diel_fref * 1e9, progress=progress, log=self.log)
        for f, lm in (self.levels or [])[:-1]:                     # convergence: coarser levels first, no dumps
            self.log(f"--- mesh level x{f:g} ({lm['info']['cells'] / 1e6:.2f} M cells) ---")
            r = run_excitations(m, lm, self.freq, self.excite, self.bc,
                                os.path.join(a.out, 'converge', f"L{f:g}"), **run_kw)
            fill_reciprocal(r.S, self.excite)
            self.coarse.append((f, mixed_mode(r.S, m)[0]))
        if self.levels:
            self.log(f"--- mesh level x{self.levels[-1][0]:g} (finest: the result) ---")
        xml = self.path('.xml')
        if self.antenna:
            from .antenna import run_antenna
            from .simulation import RunResult
            ff = [float(v) * 1e9 for v in str(a.ff_freq).split(',')] if a.ff_freq else []
            n = len(m.ports)
            k = self.excite[0]
            self.result = RunResult(np.full((len(self.freq), n, n), np.nan + 0j))
            self.result.S[:, :, k], self.ant = run_antenna(
                m, self.grid, self.freq, k, self.bc, a.out, ff_extra=ff, ff_pattern=ff[0] if ff else None,
                end_db=a.end_db, sigma=a.sigma, threads=a.threads, xml_path=xml, dumps=self.dumps, diel=a.diel,
                diel_fref=a.diel_fref * 1e9, step=a.ff_step, max_steps=a.max_steps, progress=progress, log=self.log)
            self.result.seconds[m.ports[k].name] = self.ant['seconds']
            self.result.steps[m.ports[k].name] = self.ant['steps']
        else:
            self.result = run_excitations(m, self.grid, self.freq, self.excite, self.bc, a.out, xml_path=xml,
                                          dumps=self.dumps, dump_runs=dump_runs, **run_kw)
        self.wrote('CSX geometry (AppCSXCAD)', xml)

    def outputs(self):
        a, m, name, freq = self.a, self.m, self.name, self.freq
        r = self.result
        self.dump_pngs = []
        for (pname, dname), path in r.dump_files.items():
            d = next(x for x in self.dumps if x.name == dname)
            if d.freqs and os.path.exists(path):
                tag = pname.replace('+', 'p').replace('-', 'n')
                pngs = plot_fd_dumps(d, path, m, self.path(f"_{tag}"), norm=r.dump_norm.get(pname), port=pname,
                                     log=self.log)
                self.dump_pngs += [(f"{dname} ({pname} driven)", p) for p in pngs]
                self.log(f"  {dname} ({pname} driven): {path}" + (f" -> {len(pngs)} PNG map(s)" if pngs else ''))
            else:
                self.log(f"  {dname} ({pname} driven): {path}  (time domain: open the .vtr series in ParaView)")
        missing = fill_reciprocal(r.S, self.excite)
        if missing:
            self.log(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the "
                     f"terms between unexcited ports are unknown (written as 0)")
        self.Smm, self.names, zref = mixed_mode(r.S, m)
        out = self.path(f"_openems.s{len(self.names)}p")
        write_touchstone(out, freq, np.nan_to_num(self.Smm), zref, comment=f"openEMS FDTD ({name})", names=self.names)
        self.wrote('S-parameters (Touchstone)', out)
        cmp = (*read_touchstone(a.compare), os.path.basename(a.compare)) if a.compare else None
        png = self.path('_openems.png')
        plot_results(png, freq, self.Smm, self.names, f"{name} - openEMS", cmp)
        self.wrote('S-parameter plot', png)
        self.info = dict(ports=self.names, zref=zref, mesh=self.grid['info'],
                         excited=[m.ports[k].name for k in self.excite],
                         parts=[dict(ref=p.ref, value=p.value_str(), layer=p.layer) for p in m.parts], diel=a.diel,
                         runs={p: dict(seconds=r.seconds.get(p), steps=r.steps.get(p)) for p in r.seconds})
        if self.ant:
            self.antenna_outputs()
        if self.levels:
            self.convergence_outputs()
        js = self.path('_openems.json')
        with open(js, 'w') as fh:
            json.dump(dict(self.info, runtime_s=time.time() - self.t0), fh, indent=1)
        self.wrote('run summary (JSON)', js)
        self.log(f"wrote {out}, {png}  ({time.time() - self.t0:.0f} s)")

    def antenna_outputs(self):
        from .antenna import save as save_antenna
        k = self.excite[0]
        apng, ppng, js = self.path('_antenna.png'), self.path('_pattern.png'), self.path('_antenna.json')
        plot_antenna(apng, self.freq, self.result.S[:, k, k], self.m.ports[k].z0, self.ant, f"{self.name} - antenna")
        plot_pattern(ppng, self.ant, f"{self.name} - gain pattern")
        save_antenna(self.ant, js)
        self.info['antenna'] = dict(match=self.ant['match'], f_pattern=self.ant['f_pattern'])
        self.wrote('antenna: match, gain, efficiency', apng)
        self.wrote('antenna: patterns', ppng)
        self.wrote('antenna results (JSON)', js)
        self.log(f"wrote {apng}, {ppng}")

    def convergence_outputs(self):
        from .converge import compare_levels, report
        a, levels = self.a, self.levels
        tol_db, tol_deg = (float(v) for v in str(a.converge_tol).split(','))
        dev = compare_levels([S_ for _, S_ in self.coarse] + [self.Smm], self.names) + [{}]   # finest: the reference
        lines, ok = report([f for f, _ in levels], dev[:-1], tol_db, tol_deg)
        self.log("\n".join(lines))
        if not ok:
            self.log("WARNING: mesh not converged (see the convergence table)")
        png = self.path('_converge.png')
        plot_convergence(png, self.freq, [(f"x{f:g}", S_) for f, S_ in self.coarse] + [(f"x{levels[-1][0]:g}", self.Smm)],
                         self.names, f"{self.name} - mesh convergence")
        self.info['convergence'] = dict(converged=ok, tol_db=tol_db, tol_deg=tol_deg,
                                        levels=[dict(factor=f, cells=lm['info']['cells'], **d)
                                                for (f, lm), d in zip(levels, dev)])
        self.wrote('mesh convergence', png)
        self.log(f"wrote {png}")

    def report(self):
        """<name>_report.html (unless --no-report)."""
        if getattr(self.a, 'no_report', False):
            return
        from .report import write_report
        path = write_report(self)
        self.wrote('report', path)
        self.log(f"wrote {path}")
