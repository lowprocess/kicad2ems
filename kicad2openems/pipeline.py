"""
One run of kicad2openems as a sequence of stages, driven by a config.Settings:

    Job(settings).run()
        model()     KiCadBoard -> Model (stack, copper, vias, ports, lumped parts)
        mesh()      boundaries, automatic mesh (or the --converge levels), cavity check, field-dump specs
        lines()     transmission-line sections and Z0 (tlines.py); with --fast, their S-parameters (fastsolve.py)
        preview()   <name>_mesh.png, excitation length; --preview stops here (after the report)
        solve()     openEMS runs: S-parameters (all excited ports, --jobs), --converge levels, or antenna mode
        outputs()   Touchstone, plots, field-dump maps, antenna / convergence results, line-model comparison
        report()    <name>_openems.json (the run summary) and <name>_report.html built from it

Everything the report shows is collected in Job.summary, a JSON-friendly dict written to
<name>_openems.json; report.py renders that dict and nothing else. Every message goes through Job.log
(printed, kept for the report, and sent to --events as JSON lines).
"""
import datetime, json, os, time
import numpy as np

from . import __version__
from .kicad.board import KiCadBoard
from .model import Model
from .boundaries import resolve_bc, cavity_check
from .mesh import auto_mesh
CUT_KEEPOUT = 2.0      # mm: edge walls on a region cut stay this far from the signal copper (docs/model.md)
from .plots import save, preview, plot_results, plot_convergence, plot_antenna, plot_pattern, plot_lines
from .sparams import mixed_mode, fill_reciprocal
from .touchstone import write_touchstone, read_touchstone
from .dumps import parse_dump, estimate_size_mb, plot_fd_dumps


def ram_gb():
    """Installed RAM in GB (None where sysconf cannot tell)."""
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1e9
    except (ValueError, OSError, AttributeError):
        return None


def _db(v):
    return float(20 * np.log10(abs(v) + 1e-15)) if np.isfinite(v) else None


def sparam_table(freq, S, names):
    """Per S-parameter (lower triangle): |S| in dB at fmin, the centre and fmax, and its extremes."""
    rows, ic = [], len(freq) // 2
    for j in range(S.shape[1]):
        for i in range(j, S.shape[1]):
            s = S[:, i, j]
            ok = np.isfinite(s)
            if not ok.any():
                continue
            db = 20 * np.log10(np.abs(s[ok]) + 1e-15)
            fb = freq[ok]
            rows.append(dict(param=f"S({names[i]},{names[j]})", fmin_db=_db(s[0]), fc_db=_db(s[ic]), fmax_db=_db(s[-1]),
                             min_db=float(db.min()), f_min=float(fb[np.argmin(db)]),
                             max_db=float(db.max()), f_max=float(fb[np.argmax(db)])))
    return rows


def compare_table(freq, S, Sx, names, floor_db=-40.0):
    """openEMS (S) against another result (Sx) on the same frequencies: per S-parameter the largest
    vector difference |S - Sx| and the maxima in dB; for transmissions also the largest |S| difference
    in dB (where openEMS is above floor_db) and the delay difference (Sx - S, from the phase slope) in
    ps. (A reflection's dB difference is dominated by where its nulls fall, so it is left out.)"""
    rows = []
    w = 2 * np.pi * freq
    for j in range(S.shape[1]):
        for i in range(j, S.shape[1]):
            a, b = S[:, i, j], Sx[:, i, j]
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() < 3:
                continue
            da, dbx = 20 * np.log10(np.abs(a[ok]) + 1e-15), 20 * np.log10(np.abs(b[ok]) + 1e-15)
            m = da > floor_db
            row = dict(param=f"S({names[i]},{names[j]})",
                       max_ddb=float(np.max(np.abs(dbx - da)[m])) if m.any() and i != j else None,
                       max_dvec=float(np.max(np.abs(b[ok] - a[ok]))), openems_max_db=float(da.max()),
                       other_max_db=float(dbx.max()), ddelay_ps=None)
            if i != j and m.all():
                slope = lambda s: -np.polyfit(w[ok], np.unwrap(np.angle(s[ok])), 1)[0]
                row['ddelay_ps'] = float((slope(b) - slope(a)) * 1e12)
            rows.append(row)
    return rows


class Job:
    def __init__(self, settings, echo=print, command=None):
        self.s, self.echo = settings, echo
        self.messages, self.files = [], []
        self.name = os.path.splitext(os.path.basename(settings.board))[0]
        self.fmin, self.fmax = settings.fmin * 1e9, settings.fmax * 1e9
        self.antenna = settings.antenna
        self.t0 = time.time()
        self.levels = self.ant = self.result = self.lm = self.fast = None
        self.coarse, self.dumps = [], []
        self.events = None
        mode = 'preview' if settings.preview else ('antenna' if self.antenna else 'S-parameters')
        self.summary = dict(name=self.name, version=__version__, mode=mode, command=command,
                            created=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), settings=settings.options(),
                            images={}, files=self.files, dumps=[])

    # ---- helpers -----------------------------------------------------------------------------------
    def log(self, *msg):
        text = " ".join(str(x) for x in msg)
        self.echo(text)
        self.messages.extend(text.split("\n"))
        if self.events:
            self.events.emit('log', text=text)

    def stage(self, name):
        if self.events:
            self.events.emit('stage', name=name)

    def status(self, run, st):
        if self.events:                                              # openEMS's own clock 't' -> 'run_time'
            self.events.emit('status', run=run, **{('run_time' if k == 't' else k): v for k, v in st.items()})

    @property
    def warnings(self):
        return [l.strip() for l in self.messages if 'WARNING' in l or l.strip().lower().startswith('warning')]

    def path(self, suffix):
        return os.path.join(self.s.out, f"{self.name}{suffix}")

    def rel(self, path):
        """A path as the summary stores it: relative to the output folder (the JSON stays valid when the
        folder moves)."""
        try:
            return os.path.relpath(path, self.s.out)
        except ValueError:                                           # another drive (Windows)
            return path

    def wrote(self, label, path):
        self.files.append((label, self.rel(path)))
        if self.events:
            self.events.emit('file', label=label, path=path)

    def figure(self, fig, suffix, key, label):
        """Save a plot as <name><suffix>, record it for the report."""
        if fig is None:
            return None
        p = save(fig, self.path(suffix))
        self.summary['images'][key] = self.rel(p)
        self.wrote(label, p)
        return p

    # ---- stages --------------------------------------------------------------------------------------
    def run(self):
        os.makedirs(self.s.out, exist_ok=True)
        if self.s.events:
            from .events import EventSink
            os.makedirs(os.path.dirname(os.path.abspath(self.s.events)), exist_ok=True)
            self.events = EventSink(self.s.events)
        try:
            code = self._run()
        finally:
            if self.events:
                self.events.emit('done', code=locals().get('code'))
                self.events.close()
        return code

    def _run(self):
        for st in (self.model, self.mesh, self.lines, self.preview):
            self.stage(st.__name__)
            st()
        if self.s.preview:
            self.report()
            return 0
        from .simulation import check_openems
        ok, lines = check_openems()
        if not ok:
            self.log("openEMS cannot be imported - details:\n" + "\n".join(lines))
            return 2
        for st in (self.solve, self.outputs):
            self.stage(st.__name__)
            st()
        self.report()
        return 0

    def model(self):
        s = self.s
        self.board = KiCadBoard(s.board)
        self.m = m = Model(self.board, nets=s.nets, pos_file=s.pos, include_mask=s.mask, region=s.region,
                           ports=s.ports, cut_ports=s.cut_ports,
                           ground=s.ground, parts=s.parts, log=self.log)
        self.log(m.describe())
        if s.diel == 'wideband':
            from .dielectric import describe as describe_diel
            self.log("\n".join(describe_diel(m.layers, s.diel_fref * 1e9, self.fmin, self.fmax)))
        ob = m.outline.bounds
        self.summary['model'] = dict(
            board=s.board, outline_mm=[ob[2] - ob[0], ob[3] - ob[1]], nets=s.nets or 'all', diel=s.diel,
            vias=len(m.vias), vias_signal=int(sum(m.via_signal)), signal_nets=sorted(m.signal_nets),
            stack=[dict(name=l['name'], z0=l['z0'], z1=l['z1'], er=l['er'], tand=l['tand']) for l in m.layers[::-1]],
            copper=[dict(layer=n, z=m.cu_z[n], modelled=n in m.copper) for n in m.cu_order[::-1]],
            ports=[dict(name=p.name, kind=p.kind, layer=p.layer, ref_layer=p.ref_layer, w=p.w, z0=p.z0,
                        h=abs(p.z_sig - p.z_ref), at=[float(p.c[0]), float(-p.c[1])], direction=p.direction,
                        gap=(p.gap[1] - p.gap[0]) if p.kind == 'gap' else None) for p in m.ports],
            parts=[dict(ref=p.ref, value=p.value_str(), layer=p.layer, gap=p.gap, elements=len(p.pieces()))
                   for p in m.parts],
            notes=list(m.notes))

    def mesh(self):
        s, m = self.s, self.m
        air, bc = s.air, s.bc
        if self.antenna:
            from .antenna import antenna_air
            air = antenna_air(self.fmin, self.fmax, s.air)
            bc = 'PML_8' if bc == 'auto' else bc
            self.log(f"antenna mode: boundaries {bc}, air margin {air:.1f} mm")
        self.bc = resolve_bc(bc, m)
        kw = dict(air=air, ratio=s.ratio, nz_min=s.nz, bc=self.bc, curves=s.curves, vias=s.vias,
                  via_snap=s.via_snap, log=self.log)
        if s.converge:
            from .converge import mesh_levels, describe_levels
            self.levels = mesh_levels(m, self.fmin, self.fmax, sorted(set(s.converge)), res=s.res, fine=s.fine, **kw)
            self.log("\n".join(describe_levels(self.levels)))
            self.grid = self.levels[-1][1]                          # the finest level gives the result
            self.summary['levels'] = [dict(factor=f, cells=lm['info']['cells'], fine_mm=lm['info']['fine_mm'],
                                           dt_ps=lm['info']['dt_ps']) for f, lm in self.levels]
        else:
            self.grid = auto_mesh(m, self.fmin, self.fmax, res=s.res, fine=s.fine, **kw)
        sides, keepout = s.edge_walls, s.edge_keepout
        if not sides and s.cut_ports and getattr(m, 'cut_sides', None):
            # a cut through a plane pair leaves it open at the port: close it, well clear of the lines
            sides, keepout = m.cut_sides, max(s.edge_keepout, CUT_KEEPOUT)
            self.log(f"edge walls on the region cut ({', '.join(sides)}), {keepout:g} mm clear of the signal copper")
        if sides:
            from .edges import edge_walls, describe, snap
            walls = edge_walls(m, sides, keepout=keepout)
            self.log(describe(walls, m))
            for g in [self.grid] + [lm for _, lm in (self.levels or [])]:
                g['walls'] = snap(walls, g)
                g['info']['edge_walls'] = describe(walls, m).split(': ', 1)[1]
        self.summary['mesh'] = self.grid['info']
        cavity_check(m, self.grid, self.bc, self.fmax, log=self.log)
        self.dumps = [parse_dump(sp, m, self.grid) for sp in s.dump]   # validated before any run
        for d in self.dumps:
            self.log("  " + d.describe(self.grid))

    def lines(self):
        """Transmission-line sections (always) and, with --fast, their S-parameters. A ballpark aid: whatever
        goes wrong here is a warning, never the end of the run."""
        try:
            self._lines()
        except Exception as e:
            self.log(f"WARNING: transmission-line model failed: {type(e).__name__}: {e}")
            self.lm = self.fast = None
            self.summary.pop('fast', None)

    def _lines(self):
        from .tlines import build
        self.lm = build(self.m)
        if not self.lm.paths:
            self.log("transmission lines: no routed path from the ports" +
                     (f" ({'; '.join(self.lm.notes)})" if self.lm.notes else ''))
            return
        self.summary['lines'] = self.lm.summary()
        for p, q, secs, branches in self.lm.paths:
            zs = [e.z0 for e, _, _ in secs if e.z0]
            self.log(f"transmission line {p} -> {q or 'end'}: {len(secs)} sections, "
                     f"{sum(e.length for e, _, _ in secs):.2f} mm" +
                     (f", Z0 {min(zs):.1f}..{max(zs):.1f} ohm" if zs else '') +
                     (f", {len(branches)} branch(es)" if branches else ''))
        if self.lm.groups or self.lm.junctions:
            from collections import Counter
            cnt = Counter(j['kind'] for j in self.lm.junctions)
            self.log(f"  {len(self.lm.groups)} coupled section(s)" +
                     (" (" + ", ".join(f"{len(g['members'])} lines {g['length']:.2f} mm" for g in self.lm.groups[:6]) +
                      (", ..." if len(self.lm.groups) > 6 else "") + ")" if self.lm.groups else "") +
                     "; discontinuities: " + (", ".join(f"{n} {k}" for k, n in sorted(cnt.items())) or "none"))
        self.figure(plot_lines(self.lm, self.m, self.name), '_tlines.png', 'lines', 'transmission-line sections')
        if self.s.fast:
            self.fast_solve()

    def fast_solve(self):
        from .fastsolve import solve
        s, m = self.s, self.m
        self.freq = np.linspace(self.fmin, self.fmax, s.points)
        try:
            S, seconds = solve(self.lm, m.ports, self.freq, backend=s.fast, sigma=s.sigma, qucsator=s.qucsator)
        except (ValueError, RuntimeError) as e:                       # missing package / program: say how to fix
            self.log(f"WARNING: fast line model ({s.fast}) failed: {e}")
            return
        Smm, names, zref = mixed_mode(S, m)
        out = self.path(f"_fast.s{len(names)}p")
        write_touchstone(out, self.freq, np.nan_to_num(Smm), zref, comment=f"line model, {s.fast} ({self.name})",
                         names=names)
        self.wrote(f'S-parameters, fast line model ({s.fast})', out)
        self.fast = dict(S=Smm, names=names, backend=s.fast, seconds=seconds)
        self.summary['fast'] = dict(backend=s.fast, seconds=seconds, touchstone=self.rel(out),
                                    table=sparam_table(self.freq, Smm, names))
        self.log(f"fast line model ({s.fast}): {seconds * 1e3:.0f} ms -> {out}")

    def preview(self):
        from .simulation import pulse_steps
        from .antenna import exc_fmax
        s = self.s
        self.figure(preview(self.m, self.grid, self.name), '_mesh.png', 'mesh', 'model and mesh')
        self.log(f"wrote {self.path('_mesh.png')}")
        ps = pulse_steps(self.fmin, self.fmax, self.grid['info']['dt_ps'],
                         exc_fmax(self.fmin, self.fmax) if self.antenna else None)
        self.summary['pulse_steps'] = ps
        self.log(f"excitation pulse ~{ps} timesteps (estimate); --max-steps {s.max_steps} allows "
                 f"{s.max_steps / ps:.0f} pulse lengths" +
                 (" - WARNING: few for the fields to ring down: raise --max-steps" if s.max_steps < 20 * ps else ''))
        if self.fast and s.preview:                                  # nothing else to show it against
            self.figure(plot_results(self.freq, self.fast['S'], self.fast['names'],
                                     f"{self.name} - fast line model ({s.fast})", label=f"line model ({s.fast})"),
                        '_fast.png', 'fast', 'S-parameter plot, fast line model')

    def solve(self):
        from .simulation import run_excitations, port_index
        s, m = self.s, self.m
        self.excite = port_index(m, s.excite)
        if self.antenna and len(self.excite) > 1:
            self.log(f"antenna mode: driving {m.ports[self.excite[0]].name} only (one port per antenna run; "
                     f"--excite to choose)")
            self.excite = self.excite[:1]
        dump_runs = port_index(m, s.dump_port) if s.dump_port else None
        if self.dumps:
            n_runs = len(dump_runs) if dump_runs else 1
            self.log(f"field dumps: ~{estimate_size_mb(self.dumps, self.grid) * n_runs:.0f} MB on disk (rough), in "
                     f"{n_runs} run(s)")
        self.freq = np.linspace(self.fmin, self.fmax, s.points)
        jobs = max(1, min(s.jobs, len(self.excite)))
        ram = ram_gb()
        if jobs > 1 and ram and jobs * self.grid['info']['mem_gb'] > 0.8 * ram:
            self.log(f"WARNING: {jobs} parallel runs need ~{jobs * self.grid['info']['mem_gb']:.1f} GB, "
                     f"{ram:.1f} GB RAM installed - use fewer --jobs")
        progress = dict(mode='raw' if s.openems_output else 'bar', on_status=self.status if self.events else None)
        run_kw = dict(end_db=s.end_db, end_on=s.end_on, sigma=s.sigma, threads=s.threads, diel=s.diel, max_steps=s.max_steps,
                      diel_fref=s.diel_fref * 1e9, progress=progress, log=self.log)
        for f, lm in (self.levels or [])[:-1]:                     # convergence: coarser levels first, no dumps
            self.log(f"--- mesh level x{f:g} ({lm['info']['cells'] / 1e6:.2f} M cells) ---")
            r = run_excitations(m, lm, self.freq, self.excite, self.bc, os.path.join(s.out, 'converge', f"L{f:g}"),
                                jobs=jobs, **run_kw)
            fill_reciprocal(r.S, self.excite)
            self.coarse.append((f, mixed_mode(r.S, m)[0]))
        if self.levels:
            self.log(f"--- mesh level x{self.levels[-1][0]:g} (finest: the result) ---")
        xml = self.path('.xml')
        if self.antenna:
            from .antenna import run_antenna
            k = self.excite[0]
            self.result = run_antenna(m, self.grid, self.freq, k, self.bc, s.out,
                                      ff_extra=[v * 1e9 for v in s.ff_freq], ff_pattern=s.ff_freq[0] * 1e9 if s.ff_freq else None,
                                      step=s.ff_step, xml_path=xml, dumps=self.dumps, **run_kw)
            self.ant = self.result.extra.get(m.ports[k].name)
        else:
            self.result = run_excitations(m, self.grid, self.freq, self.excite, self.bc, s.out, xml_path=xml,
                                          dumps=self.dumps, dump_runs=dump_runs, jobs=jobs, **run_kw)
        self.wrote('CSX geometry (AppCSXCAD)', xml)

    def outputs(self):
        from .simulation import run_dir, log_path
        s, m, name, freq = self.s, self.m, self.name, self.freq
        r = self.result
        for (pname, dname), path in r.dump_files.items():
            d = next(x for x in self.dumps if x.name == dname)
            if d.freqs and os.path.exists(path):
                tag = pname.replace('+', 'p').replace('-', 'n')
                pngs = plot_fd_dumps(d, path, m, self.path(f"_{tag}"), norm=r.dump_norm.get(pname), port=pname,
                                     log=self.log)
                self.summary['dumps'] += [dict(label=f"{dname} ({pname} driven)", png=self.rel(p)) for p in pngs]
                for p in pngs:
                    self.wrote(f"field map {dname} ({pname})", p)
                self.log(f"  {dname} ({pname} driven): {path}" + (f" -> {len(pngs)} PNG map(s)" if pngs else
                                                                   "  (volume: the GUI's Fields tab or ParaView)"))
            else:
                self.log(f"  {dname} ({pname} driven): {path}  (time domain: the GUI's Fields tab or ParaView)")
        missing = fill_reciprocal(r.S, self.excite)
        if missing:
            self.log(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the "
                     f"terms between unexcited ports are unknown (written as 0)")
        self.Smm, self.names, zref = mixed_mode(r.S, m)
        out = self.path(f"_openems.s{len(self.names)}p")
        write_touchstone(out, freq, np.nan_to_num(self.Smm), zref, comment=f"openEMS FDTD ({name})", names=self.names)
        self.wrote('S-parameters (Touchstone)', out)
        overlays = []
        if s.compare:
            fc, Sc = read_touchstone(s.compare)
            overlays.append((fc, Sc, os.path.basename(s.compare), '--'))
        if self.fast:
            overlays.append((freq, self.fast['S'], f"line model ({self.fast['backend']})", ':'))
            self.summary['fast']['compare'] = compare_table(freq, self.Smm, self.fast['S'], self.names)
        self.figure(plot_results(freq, self.Smm, self.names, f"{name} - openEMS", overlays), '_openems.png',
                    'sparams', 'S-parameter plot')
        runs = {p: dict(seconds=r.seconds.get(p), steps=r.steps.get(p), ended=getattr(r, 'ended', {}).get(p))
                for p in r.seconds}
        for k in self.excite:
            runs.setdefault(m.ports[k].name, {})['log'] = self.rel(log_path(run_dir(m, k, s.out)))
        self.summary.update(
            results=dict(names=self.names, zref=zref, excited=[m.ports[k].name for k in self.excite],
                         freq_ghz=[freq[0] / 1e9, freq[len(freq) // 2] / 1e9, freq[-1] / 1e9],
                         table=sparam_table(freq, self.Smm, self.names), compare_file=s.compare),
            runs=runs)
        if self.ant:
            self.antenna_outputs()
        if self.levels:
            self.convergence_outputs()

    def antenna_outputs(self):
        from .antenna import save as save_antenna
        k = self.excite[0]
        self.figure(plot_antenna(self.freq, self.result.S[:, k, k], self.m.ports[k].z0, self.ant,
                                 f"{self.name} - antenna"), '_antenna.png', 'antenna', 'antenna: match, gain, efficiency')
        self.figure(plot_pattern(self.ant, f"{self.name} - gain pattern"), '_pattern.png', 'pattern', 'antenna: patterns')
        js = self.path('_antenna.json')
        save_antenna(self.ant, js)
        self.wrote('antenna results (JSON)', js)
        self.summary['antenna'] = {k_: v for k_, v in self.ant.items() if k_ != 'sphere'}

    def convergence_outputs(self):
        from .converge import compare_levels, verdict
        s, levels = self.s, self.levels
        tol_db, tol_deg = s.converge_tol
        dev = compare_levels([S_ for _, S_ in self.coarse] + [self.Smm], self.names) + [{}]   # finest: the reference
        lines, ok = verdict([f for f, _ in levels], dev[:-1], tol_db, tol_deg)
        self.log("\n".join(lines))
        if not ok:
            self.log("WARNING: mesh not converged (see the convergence table)")
        self.figure(plot_convergence(self.freq, [(f"x{f:g}", S_) for f, S_ in self.coarse] +
                                     [(f"x{levels[-1][0]:g}", self.Smm)], self.names, f"{self.name} - mesh convergence"),
                    '_converge.png', 'converge', 'mesh convergence')
        self.summary['convergence'] = dict(converged=ok, tol_db=tol_db, tol_deg=tol_deg,
                                           levels=[dict(factor=f, cells=lm['info']['cells'], **d)
                                                   for (f, lm), d in zip(levels, dev)])

    def report(self):
        """<name>_openems.json (the run summary) and <name>_report.html (unless --no-report)."""
        js = self.path('_openems.json')
        self.wrote('run summary (JSON)', js)
        if not self.s.no_report:
            self.wrote('report', self.path('_report.html'))
        sm = self.summary
        sm.update(warnings=self.warnings, log=list(self.messages), runtime_s=time.time() - self.t0)
        if 'results' in sm:                                          # the keys older readers use
            sm.update(ports=sm['results']['names'], zref=sm['results']['zref'], excited=sm['results']['excited'],
                      parts=sm['model']['parts'], diel=self.s.diel)
        with open(js, 'w') as fh:
            json.dump(sm, fh, indent=1, default=_plain)
        if not self.s.no_report:
            from .report import write_report
            write_report(sm, self.path('_report.html'))
            self.log(f"wrote {self.path('_report.html')}")
        self.log(f"done ({time.time() - self.t0:.0f} s)")


def _plain(v):
    """numpy values in the summary -> JSON."""
    if isinstance(v, np.ndarray):
        return v.tolist()
    try:
        return v.item()
    except AttributeError:
        return str(v)
