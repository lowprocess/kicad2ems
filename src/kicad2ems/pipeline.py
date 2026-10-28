"""
One run of kicad2ems as a sequence of stages, driven by a config.Settings, over the solver plugins (solvers/):

    Job(settings).run()
        preflight() the solvers of the run (Settings.solver_plan; 'lines' resolved to qucs or scikit), each
                    checked before any work: one named in `solvers` that cannot run stops the run here
        model()     the board file -> Board (stack, copper, vias, ports, lumped parts, edge walls), written
                    as <name>_board.json: what every solver gets
        lines()     the transmission-line model (lines/tlines.py): sections, Z0, coupled groups, discontinuities;
                    what the line solvers (qucs, scikit) solve
        choose()    --solvers auto: the full-wave solver estimated cheapest for this board (Solver.estimate) among
                    those that can run and model it, plus the line model; why, in the log and the summary
        assess()    each solver against this board and run: one named that cannot model it stops the run, a
                    reference is left out with a warning
        prepare()   each solver lowers the board to its own form (openEMS: boundaries, mesh and the mesh preview;
                    with --preview it is prepared, not solved) and declares what it changes (deviations: vias
                    moved, parts split, walls as via rows), the report's "What each solver saw"; without
                    openEMS, <name>_model.png
        solve()     the solvers, the cheap ones first: S-parameters over the board's ports
        outputs()   <name>_<solver>.sNp per solver; the first solver's plot <name>_<solver>.png with the others
                    (and --compare) overlaid, and each compared with it; each solver's own outputs (openEMS:
                    field maps, antenna, convergence)
        report()    <name>_run.json (the run summary) and <name>_report.html built from it

Everything the report shows is collected in Job.summary, a JSON-friendly dict written to <name>_run.json;
report.py renders that dict and nothing else. Every message goes through Job.log (printed, kept for the
report, and sent to --events as JSON lines).
"""
import dataclasses, datetime, json, os, time
import numpy as np

from . import __version__, stop
from .board import Board
from .plots import save, preview, plot_results, plot_lines
from .touchstone import write_touchstone, read_touchstone
from .solvers import registry, label, resolve_lines, missing, LINES, AUTO, MOM, FEATURES
from .parity import describe


def ram_gb():
    """Installed RAM in GB (None where sysconf cannot tell)."""
    from emsdev.memory import total
    t = total()
    return t / 1e9 if t else None


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


def unknown_note(S, names):
    """'; not computed (written as 0): S(P2,P2), ...' for the Touchstone header: the terms that are NaN (ports
    not driven, or no path between two ports in the line model), which a Touchstone file cannot leave out."""
    bad = ~np.isfinite(S).all(0)
    terms = [f"S({names[i]},{names[j]})" for i in range(len(names)) for j in range(len(names)) if bad[i, j]]
    return f"; not computed (written as 0): {', '.join(terms)}" if terms else ''


def compare_table(freq, S, Sx, names, floor_db=-40.0):
    """A reference result (S) against another (Sx) on the same frequencies: per S-parameter the largest
    vector difference |S - Sx| and the maxima in dB; for transmissions also the largest |S| difference
    in dB (where the reference is above floor_db) and the delay difference (Sx - S, from the phase slope) in
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
                       max_dvec=float(np.max(np.abs(b[ok] - a[ok]))), ref_max_db=float(da.max()),
                       other_max_db=float(dbx.max()), ddelay_ps=None)
            if i != j and m.all():
                slope = lambda s: -np.polyfit(w[ok], np.unwrap(np.angle(s[ok])), 1)[0]
                row['ddelay_ps'] = float((slope(b) - slope(a)) * 1e12)
            rows.append(row)
    return rows


OVERLAY_STYLES = (':', '-.', (0, (1, 1)), (0, (5, 1)), (0, (3, 1, 1, 1)))    # the other solvers on the plot


def saw_table(m, s, names, deviations):
    """The report's "What each solver saw": per feature of the board, each solver's capability (yes / no / ignored /
    approximated: how) and what it declared changing there."""
    reg = registry()
    half = 0.5 * m.outline.area
    applies = dict(gap_ports=any(p.kind == 'gap' for p in m.ports), diff_pairs=any(p.pair for p in m.ports),
                   parts=bool(m.parts), vias=bool(m.vias), edge_walls=bool(m.walls), conductor_loss=bool(s.sigma),
                   planes=any(sum(p.area for p in ps) >= half for ps in m.copper.values()), antenna=s.antenna)
    rows = [dict(feature=text, cells=[dict(cap=reg[n].capabilities.get(k, '?'),
                                           deviations=describe([d for d in deviations.get(n, []) if d.feature == k]))
                                      for n in names])
            for k, text in FEATURES.items() if applies.get(k, True)]
    return dict(solvers=[reg[n].label for n in names], rows=rows)


def board_from_settings(s, log=print):
    """The Board of a run's settings: the board file through its importer, resolved with the selection
    settings - or a board.json written by an earlier run, as it is (its selection is already applied)."""
    if s.board.lower().endswith('.json'):
        b = Board.load(s.board, log=log)
        given = [k for k in ('nets', 'pos', 'region', 'ground', 'ports', 'cut_ports', 'edge_walls')
                 if getattr(s, k) not in (None, False, [], ())]
        if given:
            log(f"note: {os.path.basename(s.board)} is a resolved board: its own selection holds, not "
                f"{', '.join(given)} from the settings")
        return b
    from .importers import open_board
    return Board(open_board(s.board, pos=s.pos), nets=s.nets, pos_file=s.pos, include_mask=s.mask, region=s.region, ports=s.ports,
                 cut_ports=s.cut_ports, ground=s.ground, parts=s.parts, edge_walls=s.edge_walls,
                 edge_keepout=s.edge_keepout, log=log)


class Job:
    def __init__(self, settings, echo=print, command=None):
        from .config import default_out
        if not settings.out:
            settings.out = default_out(settings.board)
        self.s, self.echo = settings, echo
        self.messages, self.files = [], []
        self.name = os.path.splitext(os.path.basename(settings.board))[0]
        if settings.board.lower().endswith('.json') and self.name.endswith('_board'):
            self.name = self.name[:-len('_board')]                 # <name>_board.json of an earlier run
        self.fmin, self.fmax = settings.fmin * 1e9, settings.fmax * 1e9
        self.freq = np.linspace(self.fmin, self.fmax, settings.points)
        self.antenna = settings.antenna
        self.t0 = time.time()
        self.lm = self.m = None
        self.events = None
        self.plan, self.named = settings.solver_plan()              # the solvers of this run; those named in solvers
        # solvers --preview prepares without solving them (openEMS: the mesh preview)
        self.previewed = [x for x in settings.solver_plan(preview=False)[0] if x not in self.plan]
        self.prep, self.results, self.deviations = {}, {}, {}        # per solver: prepare()'s form, its Result, what it changes
        mode = 'preview' if settings.preview else ('antenna' if self.antenna else 'S-parameters')
        self.summary = dict(name=self.name, version=__version__, mode=mode, command=command, solvers=list(self.plan),
                            created=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), settings=settings.options(),
                            images={}, files=self.files, dumps=[], solved={})

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
        stop.arm(self.s.out)                                         # the GUI's Stop: a STOP file there (stop.py)
        if self.s.events:
            from .events import EventSink
            os.makedirs(os.path.dirname(os.path.abspath(self.s.events)), exist_ok=True)
            self.events = EventSink(self.s.events)
        try:
            code = self._run()
        finally:
            stop.disarm()
            if self.events:
                self.events.emit('done', code=locals().get('code'))
                self.events.close()
        return code

    def _run(self):
        if not self.preflight():
            return 2
        for st in (self.model, self.lines):
            self.stage(st.__name__)
            st()
        if not self.choose() or not self.assess():
            return 2
        for st in (self.prepare, self.solve, self.outputs):
            self.stage(st.__name__)
            st()
        self.report()
        failed = [label(x) for x in self.named if x in self.plan and x not in self.results]
        if failed and stop.requested():
            return 1
        if failed:
            self.log(f"failed: {', '.join(failed)} gave no result (see the warnings above)")
            return 1
        return 0

    def _drop(self, name, why, what='cannot run'):
        """A solver that cannot take part: False (the run stops) when it is named in `solvers`, else it is left
        out with a warning."""
        if name in self.named:
            self.log(f"{name} {what}: {why}")
            return False
        self.log(f"WARNING: {name} {what}, left out: {why}")
        self.plan.remove(name)
        return True

    def preflight(self):
        """Before any work: can the solvers of this run run? One named in `solvers` that cannot stops the run
        here (False); a reference added by --mom / --fast is left out with a warning."""
        s, reg = self.s, registry()
        if LINES in self.plan:                                      # the line model: qucs if installed, else scikit
            line = resolve_lines(s)
            self.plan = [line if x == LINES else x for x in self.plan if x != line or LINES not in self.plan]
            self.named = [line if x == LINES else x for x in self.named if x != line or LINES not in self.named]
            self.log(f"line model: solved with {reg[line].tool} (lines: qucsator if installed, else scikit-rf)")
        if self.plan:
            self.log("solvers: " + ", ".join(label(x) for x in self.plan))
        for name in [x for x in self.plan if x != AUTO]:
            ok, what = reg[name].check(s)
            if ok:
                continue
            details = reg[name].details()[1] if name in self.named else [what]
            if details != [what]:                                   # a longer check: say all of it (openEMS's import)
                self.log(f"{label(name)} cannot run - details:\n" + "\n".join(details) + "\n"
                         f"Without it: --preview (model, mesh, line sections), or another solver (--check lists them)")
                return False
            if not self._drop(name, what):
                return False
        if s.mom and MOM not in reg:                                # --mom's reference, not installed
            self.log(f"WARNING: --mom: {missing(MOM)}")
        for name in list(self.previewed):                           # a preview prepares them, solves none: warn
            sv = reg[name]
            ok, what = (True, '') if sv.previewed else sv.check(s)  # (openEMS's mesh preview needs no openEMS)
            if not ok:
                self.log(f"WARNING: {name} cannot run, left out: {what}")
                self.previewed.remove(name)
        self.summary['solvers'] = list(self.plan)
        return True

    def model(self):
        s = self.s
        self.m = m = board_from_settings(s, log=self.log)
        self.log(m.describe())
        self.wrote('board (what every solver gets)', m.save(self.path('_board.json')))
        ob = m.outline.bounds
        self.summary['model'] = dict(
            board=s.board, outline_mm=[ob[2] - ob[0], ob[3] - ob[1]], nets=s.nets or 'all', diel=getattr(s, 'diel', None),
            vias=len(m.vias), vias_signal=int(sum(m.via_signal)), signal_nets=sorted(m.signal_nets),
            stack=[dict(name=l['name'], z0=l['z0'], z1=l['z1'], er=l['er'], tand=l['tand']) for l in m.layers[::-1]],
            copper=[dict(layer=n, z=m.cu_z[n], modelled=n in m.copper) for n in m.cu_order[::-1]],
            ports=[dict(name=p.name, kind=p.kind, layer=p.layer, ref_layer=p.ref_layer, w=p.w, z0=p.z0,
                        h=abs(p.z_sig - p.z_ref), at=[float(p.c[0]), float(-p.c[1])], direction=p.direction,
                        gap=(p.gap[1] - p.gap[0]) if p.kind == 'gap' else None) for p in m.ports],
            parts=[dict(ref=p.ref, value=p.value_str(), layer=p.layer, gap=p.gap, elements=len(p.pieces()))
                   for p in m.parts],
            notes=list(m.notes))

    def lines(self):
        """The transmission-line model: sections, coupled groups, discontinuities (and their plot). A ballpark aid
        and what the line solvers solve: whatever goes wrong here is a warning, never the end of the run."""
        try:
            self._lines()
        except Exception as e:
            self.log(f"WARNING: transmission-line model failed: {type(e).__name__}: {e}")
            self.lm = None
            self.summary.pop('lines', None)

    def _lines(self):
        from .lines.builder import build
        self.lm = build(self.m)
        if not self.lm.paths:
            self.log("transmission lines: no routed path from the ports" +
                     (f" ({'; '.join(self.lm.notes)})" if self.lm.notes else ''))
            return
        self.summary['lines'] = self.lm.summary()
        out = self.path('_lines.json')                              # the sections' geometry: the GUI's overlay
        with open(out, 'w') as fh:
            json.dump(self.lm.drawing(getattr(self.m, 'cu_z', None)), fh)
        self.wrote('line model sections (JSON)', out)
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

    def choose(self):
        """--solvers auto: the full-wave solver (consumes the board) estimated cheapest among those that can run,
        can model this board and fit in memory, plus the line model where it applies (only the line model when a
        full-wave solver is named too). False: auto was named and nothing can run."""
        if AUTO not in self.plan:
            return True
        s, reg, ram = self.s, registry(), ram_gb()
        est, not_used = {}, {}
        named_fw = any(n in reg and reg[n].consumes == 'board' for n in self.named)    # one named: auto adds the line model
        for n, sv in reg.items():
            if sv.consumes != 'board' or named_fw or not sv.in_auto:
                continue
            ok, what = sv.check(s) if not (s.preview and sv.previewed) else (True, '')   # a preview only prepares it
            why = [] if ok else [f"cannot run: {what}"]
            why = why or sv.assess(self)
            if not why:
                try:
                    e = sv.estimate(self)
                except Exception as ex:                             # an estimate never stops the run
                    e, why = None, [f"no estimate: {type(ex).__name__}: {ex}"]
                if e is not None and ram and e['gb'] > 0.8 * ram:
                    why = [f"needs ~{e['gb']:.1f} GB, {ram:.0f} GB installed"]
                elif e is not None:
                    est[n] = e
            if why:
                not_used[n] = '; '.join(why)
        pick = [min(est, key=lambda n: est[n]['seconds'])] if est else []
        line = resolve_lines(s)
        if reg[line].check(s)[0] and not reg[line].assess(self):
            pick.append(line)
        if not pick:
            return self._drop(AUTO, '; '.join(f"{n}: {w}" for n, w in not_used.items()) or 'no solver applies')
        t = lambda sec: f"~{sec:.0f} s" if sec < 120 else f"~{sec / 60:.0f} min"
        self.log(f"solvers auto: {' + '.join(pick)}" + (f" ({est[pick[0]]['what']}, {t(est[pick[0]]['seconds'])})"
                                                        if pick[0] in est else '') +
                 "".join(f"; {n} not chosen ({e['what']}, {t(e['seconds'])})" for n, e in est.items() if n not in pick) +
                 "".join(f"; {n} not used: {w}" for n, w in not_used.items()) + "; force one with --solvers <name>")
        if s.preview:                                               # a preview solves no full-wave solver
            if pick[0] in est:
                self.log(f"--preview: {pick[0]} would run (without --preview); the preview has the line model and "
                         f"openEMS's mesh")
            pick = [p for p in pick if reg[p].consumes != 'board']
            self.previewed += [n for n, sv in reg.items() if sv.previewed and n not in not_used][:1]
        swap = lambda xs: [x for x in xs if x != AUTO] + [p for p in pick if p not in xs] if AUTO in xs else xs
        self.plan, self.named = swap(self.plan), swap(self.named)
        self.plan = [n for n in reg if n in self.plan]
        self.summary.update(solvers=list(self.plan), auto=dict(chosen=pick, not_used=not_used,
                                                               estimates={n: dict(e) for n, e in est.items()}))
        return True

    def assess(self):
        """Can each solver model this board in this run? (False: a named one cannot; the run stops.)"""
        for name in list(self.plan):
            why = registry()[name].assess(self)
            if why and not self._drop(name, '; '.join(why), 'cannot model this board'):
                return False
        for name in list(self.previewed):
            why = registry()[name].assess(self)
            if why:
                self.log(f"WARNING: {name} cannot model this board, left out: {'; '.join(why)}")
                self.previewed.remove(name)
        self.summary['solvers'] = list(self.plan)
        return True

    def estimate(self):
        """--preview: what each full-wave solver would cost on this board (Solver.estimate: time, host memory and,
        mom25d, GPU memory), whether it is in the run or not, logged and kept as summary['estimates'] (the GUI's
        System panel shows them against the memory there is)."""
        s, out = self.s, {}
        for n, sv in registry().items():
            if sv.consumes != 'board' or not (sv.check(s)[0] or sv.previewed) or sv.assess(self):
                continue
            try:
                e = sv.estimate(self)
            except Exception as ex:                                 # an estimate never stops a preview
                self.log(f"estimate: {n}: none ({type(ex).__name__}: {ex})")
                continue
            if e is None:
                continue
            out[n] = e = {k: v for k, v in dict(e).items() if v is not None}
            t = f"~{e['seconds']:.0f} s" if e['seconds'] < 120 else f"~{e['seconds'] / 60:.0f} min"
            self.log(f"estimate: {n}: {e['what']}, {t}, ~{e['gb']:.1f} GB" +
                     (f" + {e['gpu_gb']:.1f} GB on the GPU" if e.get('gpu_gb') else '') +
                     (f" ({e['how']})" if e.get('how') else ''))
        ram = ram_gb()
        self.summary['estimates'] = dict(solvers=out, ram_gb=ram)
        return out

    def prepare(self):
        if self.s.preview:
            self.estimate()
        names = [n for n in registry() if n in self.plan + self.previewed]
        for name in names:
            sv = registry()[name]
            self.prep[name] = sv.prepare(self)
            self.deviations[name] = devs = sv.deviations(self, self.prep[name])
            if devs:
                self.log(f"{name} changes: " + "; ".join(describe(devs)))
        self.summary['deviations'] = {n: [dataclasses.asdict(d) for d in ds] for n, ds in self.deviations.items()}
        self.summary['saw'] = saw_table(self.m, self.s, names, self.deviations)
        if not any(self.summary['images'].get(k) for k in ('mesh', 'model')):     # no mesh preview: the model alone
            self.figure(preview(self.m, None, self.name), '_model.png', 'model', 'model')

    def solve(self):
        """Each solver of the plan, the cheap ones first. A failure is a warning here; when the solver was
        named in `solvers`, the run ends with exit code 1 (see _run)."""
        order = sorted(self.plan, key=lambda n: registry()[n].rank)
        for i, name in enumerate(order):
            if stop.requested():
                self.log(f"stopped: {', '.join(label(n) for n in order[i:])} not run")
                break
            try:
                self.results[name] = registry()[name].solve(self, self.prep.get(name))
            except stop.Stopped:
                self.log(f"stopped: {label(name)} ended early, no result")
            except Exception as e:
                if os.environ.get('KICAD2EMS_TRACEBACK'):
                    raise
                self.log(f"WARNING: {name} failed: {type(e).__name__}: {e}")

    def outputs(self):
        s, freq, reg = self.s, self.freq, registry()
        done = [n for n in self.plan if n in self.results]          # registry order: the first is the reference
        if not done:
            return
        ref = self.results[done[0]]
        for n in done:
            r = self.results[n]
            out = self.path(f"_{n}.s{len(r.names)}p")
            write_touchstone(out, freq, np.nan_to_num(r.S), r.zref, names=r.names,
                             comment=r.comment + unknown_note(r.S, r.names))
            self.wrote(f"S-parameters, {reg[n].label}", out)
            entry = dict(r.info, label=reg[n].label, touchstone=self.rel(out), seconds=r.seconds, names=r.names,
                         zref=r.zref, table=sparam_table(freq, r.S, r.names))
            if n != done[0] and r.names == ref.names:
                entry.update(reference=done[0], compare=compare_table(freq, ref.S, r.S, r.names))
            self.summary['solved'][n] = entry
            self.log(f"{reg[n].label}: -> {out}")
        overlays = []
        if s.compare:
            fc, Sc = read_touchstone(s.compare)
            overlays.append((fc, Sc, os.path.basename(s.compare), '--'))
        others = [n for n in done[1:] if self.results[n].names == ref.names]
        overlays += [(freq, self.results[n].S, reg[n].label, OVERLAY_STYLES[i % len(OVERLAY_STYLES)])
                     for i, n in enumerate(others)]
        self.figure(plot_results(freq, ref.S, ref.names, f"{self.name} - {reg[done[0]].label}", overlays,
                                 label=reg[done[0]].label), f"_{done[0]}.png", 'sparams', 'S-parameter plot')
        self.summary['results'] = dict(reference=done[0], names=ref.names, zref=ref.zref,
                                       excited=ref.info.get('excited', ref.names),
                                       freq_ghz=[freq[0] / 1e9, freq[len(freq) // 2] / 1e9, freq[-1] / 1e9],
                                       table=self.summary['solved'][done[0]]['table'], compare_file=s.compare)
        for n in done:
            reg[n].outputs(self, self.prep.get(n), self.results[n])

    def report(self):
        """<name>_run.json (the run summary) and <name>_report.html (unless --no-report)."""
        js = self.path('_run.json')
        self.wrote('run summary (JSON)', js)
        if not self.s.no_report:
            self.wrote('report', self.path('_report.html'))
        sm = self.summary
        sm.update(warnings=self.warnings, log=list(self.messages), runtime_s=time.time() - self.t0)
        if 'results' in sm:                                          # the keys older readers use
            sm.update(ports=sm['results']['names'], zref=sm['results']['zref'], excited=sm['results']['excited'],
                      parts=sm['model']['parts'], diel=getattr(self.s, 'diel', None))
        from .formats import stamp
        with open(js, 'w') as fh:
            json.dump(stamp(sm, 'kicad2ems-run'), fh, indent=1, default=_plain)
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
