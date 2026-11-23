"""
One run of kicad2ems as a sequence of stages, driven by a config.Settings, over
the solvers (solvers/):

    Job(settings).run()
        preflight() the solvers of the run (Settings.solver_plan; 'lines'
                    resolved to qucs or scikit), each checked before any work:
                    one named in `solvers` that cannot run stops the run here
        model()     the board file -> Board (stack, copper, vias, ports, lumped
                    parts, edge walls), written as <name>_board.json: what every
                    solver gets
        lines()     the transmission-line model (lines/tlines.py): sections, Z0,
                    coupled groups, discontinuities; what the line solvers
                    (qucs, scikit) solve
        choose()    --solvers auto: the full-wave solver estimated cheapest for
                    this board (Solver.estimate) among those that can run and
                    model it, plus the line model; why, in the log and the
                    summary
        assess()    each solver against this board and run: one named that
                    cannot model it stops the run, a reference is left out with
                    a warning
        prepare()   each solver lowers the board to its own form (openEMS:
                    boundaries, mesh and the mesh preview; with --preview it is
                    prepared, not solved) and declares what it changes
                    (deviations: vias moved, parts split, walls as via rows),
                    the report's "What each solver saw"; without openEMS,
                    <name>_model.png
        solve()     the solvers, the cheap ones first: S-parameters over the
                    board's ports
        outputs()   <name>_<solver>.sNp per solver; the first solver's plot
                    <name>_<solver>.png with the others (and --compare)
                    overlaid, and each compared with it; each solver's own
                    outputs (openEMS: field maps, antenna, convergence)
        report()    <name>_run.json (the run summary) and <name>_report.html
                    built from it

Everything the report shows is collected in Job.summary, a JSON-friendly dict
written to <name>_run.json; report.py renders that dict and nothing else. Every
message goes through Job.log (printed, kept for the report, and sent to --events
as JSON lines).
"""
import dataclasses, datetime, json, os, sys, threading, time
from collections import Counter
import numpy as np

from . import __version__, stop
from .board import Board
from .formats import plain, stamp
from .plots import save, preview, plot_results, plot_lines
from .touchstone import write_touchstone, read_touchstone
from .solvers import registry, label, resolve_lines, LINES, AUTO, FEATURES
from .parity import describe

__all__ = ['ram_gb']


def ram_gb():
    """Installed RAM in GB (None where sysconf cannot tell)."""
    from kicad2ems.emsdev.memory import total
    t = total()
    return t / 1e9 if t else None


def _db(v):
    return float(20 * np.log10(abs(v) + 1e-15)) if np.isfinite(v) else None


def sparam_table(freq, S, names):
    """Per S-parameter (lower triangle): |S| in dB at fmin, the centre and fmax,
    and its extremes."""
    rows, ic = [], len(freq) // 2
    for j in range(S.shape[1]):
        for i in range(j, S.shape[1]):
            s = S[:, i, j]
            ok = np.isfinite(s)
            if not ok.any():
                continue
            db = 20 * np.log10(np.abs(s[ok]) + 1e-15)
            fb = freq[ok]
            rows.append(
                dict(param=f"S({names[i]},{names[j]})", fmin_db=_db(s[0]),
                     fc_db=_db(s[ic]), fmax_db=_db(s[-1]),
                     min_db=float(db.min()), f_min=float(fb[np.argmin(db)]),
                     max_db=float(db.max()), f_max=float(fb[np.argmax(db)])))
    return rows


# S's largest singular value above 1 + this: not passive (a solver's noise
# sits below)
PASSIVE_TOL = 2e-3
# a term's miss of its band-limited Kramers-Kronig reconstruction beyond the
# bound (causality.py): causal solver outputs read 0-0.06, time-reversed ones
# 1.5-4
CAUSAL_TOL = 0.1


def causality_note(freq, S, names, what):
    """(dict for the run summary, a warning or None): S's causality on its band
    (causality.check)."""
    from .causality import check
    try:
        c = check(freq, S)
    # (a check, never the run's end)
    except Exception as e:  # noqa: BLE001
        return dict(error=f"{type(e).__name__}: {e}"), None
    if c is None:
        return None, None
    i, j = c['term'] if c['term'] else (0, 0)
    out = dict(worst=round(c['worst'], 4), f=c['f'],
               term=(f"S({names[i]},{names[j]})" if c['term'] else None))
    if c['worst'] <= CAUSAL_TOL:
        return out, None
    return (out, (
        f"WARNING: {what}: not causal: {out['term']} at {c['f'] / 1e9:.4g} GHz "
        "misses its band-limited Kramers-Kronig reconstruction by "
        f"{c['worst']:.3f} beyond what any data outside the band could "
        "explain: a reference plane past the true one (de-embedding), or a "
        "sweep's fit there"
    ))


def passivity(freq, S):
    """dict(max, f, count) of S's largest singular value over the band (a
    passive network's is at most 1), its frequency and how many frequencies pass
    1 + PASSIVE_TOL; None when no frequency has every term."""
    S = np.asarray(S)
    ok = np.isfinite(S).all(axis=(1, 2))
    if not ok.any():
        return None
    sv = np.full(len(freq), np.nan)
    sv[ok] = np.linalg.svd(S[ok], compute_uv=False)[:, 0]
    k = int(np.nanargmax(sv))
    return dict(max=float(sv[k]), f=float(freq[k]), count=int(
        (sv > 1 + PASSIVE_TOL).sum()))


def make_passive(S):
    """S (nf x n x n) with each frequency's singular values above 1 clipped to 1
    (S = U diag(sv) V^H): the least change, in the 2-norm, that makes it passive
    there; (that S, how many frequencies it changed). Terms not computed (NaN)
    are left as they are, and so are their frequencies."""
    S = np.array(S, complex)
    ok = np.isfinite(S).all(axis=(1, 2))
    changed = 0
    for k in np.nonzero(ok)[0]:
        U, sv, Vh = np.linalg.svd(S[k])
        if sv[0] > 1.0:
            S[k] = (U * np.minimum(sv, 1.0)) @ Vh
            changed += 1
    return S, changed


def unknown_note(S, names):
    """'; not computed (written as 0): S(P2,P2), ...' for the Touchstone header:
    the terms that are NaN (ports not driven, or no path between two ports in
    the line model), which a Touchstone file cannot leave out."""
    bad = ~np.isfinite(S).all(0)
    terms = [
        f"S({names[i]},{names[j]})" for i in range(len(names))
        for j in range(len(names)) if bad[i, j]
    ]
    return f"; not computed (written as 0): {', '.join(terms)}" if terms else ''


def compare_table(freq, S, Sx, names, floor_db=-40.0):
    """A reference result (S) against another (Sx) on the same frequencies: per
    S-parameter the largest vector difference |S - Sx| and the maxima in dB; for
    transmissions also the largest |S| difference in dB (where the reference is
    above floor_db) and the delay difference (Sx - S, from the phase slope) in
    ps. (A reflection's dB difference is dominated by where its nulls fall, so
    it is left out.)"""
    rows = []
    w = 2 * np.pi * freq
    for j in range(S.shape[1]):
        for i in range(j, S.shape[1]):
            a, b = S[:, i, j], Sx[:, i, j]
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() < 3:
                continue
            da, dbx = (20 * np.log10(np.abs(a[ok]) + 1e-15),
                       20 * np.log10(np.abs(b[ok]) + 1e-15))
            m = da > floor_db
            row = dict(
                param=f"S({names[i]},{names[j]})",
                max_ddb=(float(np.max(np.abs(dbx - da)[m]))
                         if m.any() and i != j else None),
                max_dvec=float(np.max(np.abs(b[ok] - a[ok]))),
                ref_max_db=float(da.max()), other_max_db=float(dbx.max()),
                ddelay_ps=None)
            if i != j and m.all():
                slope = lambda s: -np.polyfit(w[ok], np.unwrap(np.angle(s[ok])),
                                              1)[0]
                row['ddelay_ps'] = float((slope(b) - slope(a)) * 1e12)
            rows.append(row)
    return rows


# the other solvers on the plot
OVERLAY_STYLES = (':', '-.', (0, (1, 1)), (0, (5, 1)), (0, (3, 1, 1, 1)))


def saw_table(m, s, names, deviations):
    """The report's "What each solver saw": per feature of the board, each
    solver's capability (yes / no / ignored / approximated: how) and what it
    declared changing there."""
    reg = registry()
    half = 0.5 * m.outline.area
    applies = dict(
        gap_ports=any(p.kind == 'gap' for p in m.ports), diff_pairs=any(
            p.pair for p in m.ports), parts=bool(m.parts), vias=bool(m.vias),
        edge_walls=bool(m.walls), conductor_loss=bool(s.sigma),
        roughness=bool(s.sigma and getattr(s, 'rough', None)),
        finish=bool(s.sigma and getattr(m, '_finish', None)
                    and any(v for v in m._finish.values())),
        planes=any(sum(p.area
                       for p in ps) >= half
                   for ps in m.copper.values()), antenna=s.antenna)
    rows = [
        dict(
            feature=text, cells=[
                dict(
                    cap=reg[n].capabilities.get(k, '?'), deviations=describe(
                        [d
                         for d in deviations.get(n, [])
                         if d.feature == k]))
                for n in names
            ])
        for k, text in FEATURES.items()
        if applies.get(k, True)
    ]
    return dict(solvers=[reg[n].label for n in names], rows=rows)


def board_from_settings(s, log=print):
    """The Board of a run's settings: the board file through its importer,
    resolved with the selection settings - or a board.json written by an earlier
    run, as it is (its selection is already applied)."""
    if s.board.lower().endswith('.json'):
        b = Board.load(s.board, log=log)
        given = [
            k for k in ('nets', 'pos', 'region', 'ground', 'ports', 'cut_ports',
                        'edge_walls')
            if getattr(s, k) not in (None, False, [], ())
        ]
        if given:
            log(f"note: {os.path.basename(s.board)} is a resolved board: its "
                f"own selection holds, not {', '.join(given)} from the "
                "settings")
        return b
    from .importers import open_board
    return Board(open_board(s.board, pos=s.pos), nets=s.nets, pos_file=s.pos,
                 include_mask=s.mask, region=s.region, ports=s.ports,
                 cut_ports=s.cut_ports, ground=s.ground, parts=s.parts,
                 edge_walls=s.edge_walls, edge_keepout=s.edge_keepout,
                 fill_voids=getattr(s, 'fill_voids', 'off'), log=log,
                 part_ports=(getattr(s, 'part_ports', None) or ()))


def _terminal_copy():
    """A copy of this process's stdout descriptor, as a text stream (None where
    stdout has none, as under pytest): what the log writes to while solvers run
    side by side, so that openEMS's redirection of descriptor 1 to its own log
    (kicad2ems.fdtd.progress.redirect_output) does not take the other solvers'
    lines."""
    try:
        sys.stdout.flush()
        return os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1,
                         encoding=(sys.stdout.encoding
                                   or 'utf-8'), errors='replace')
    except (OSError, ValueError, AttributeError):
        return None


class Job:
    def __init__(self, settings, echo=print, command=None):
        from .config import default_out
        if not settings.out:
            settings.out = default_out(settings.board)
        self.s, self.echo = settings, echo
        self.messages, self.files = [], []
        self.name = os.path.splitext(os.path.basename(settings.board))[0]
        if (settings.board.lower().endswith('.json')
                and self.name.endswith('_board')):
            # <name>_board.json of an earlier run
            self.name = self.name[:-len('_board')]
        self.fmin, self.fmax = settings.fmin * 1e9, settings.fmax * 1e9
        self.freq = (np.geomspace if getattr(settings, 'sweep', 'linear')
                     == 'log' else np.linspace)(self.fmin, self.fmax,
                                                settings.points)
        self.antenna = settings.antenna
        self.t0 = time.time()
        self.lm = self.m = self.finish = self.events = None
        # the solvers of this run; those named in solvers
        self.plan, self.named = settings.solver_plan()
        # solvers --preview prepares without solving them (openEMS: the mesh
        # preview)
        self.previewed = [
            x for x in settings.solver_plan(preview=False)[0]
            if x not in self.plan
        ]
        # per solver: prepare()'s form, its Result, what it changes
        self.prep, self.results, self.deviations = {}, {}, {}
        self.full = {}  # (_close_parts: a result over its part ports)
        mode = ('preview' if settings.preview else
                ('antenna' if self.antenna else 'S-parameters'))
        self.summary = dict(
            name=self.name, version=__version__, mode=mode, command=command,
            solvers=list(self.plan),
            created=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
            settings=settings.options(), images={}, files=self.files, dumps=[],
            solved={})

    # ---- helpers ------------------------------------------------------------
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
        if self.events:  # openEMS's own clock 't' -> 'run_time'
            self.events.emit(
                'status', run=run, **{('run_time' if k == 't' else k): v
                                      for k, v in st.items()})

    @property
    def warnings(self):
        return [
            l.strip()
            for l in self.messages
            if 'WARNING' in l or l.strip().lower().startswith('warning')
        ]

    def path(self, suffix):
        return os.path.join(self.s.out, f"{self.name}{suffix}")

    def rel(self, path):
        """A path as the summary stores it: relative to the output folder (the
        JSON stays valid when the folder moves)."""
        try:
            return os.path.relpath(path, self.s.out)
        except ValueError:  # another drive (Windows)
            return path

    def wrote(self, label, path):
        self.files.append((label, self.rel(path)))
        if self.events:
            self.events.emit('file', label=label, path=path)

    def figure(self, fig, suffix, key, label):
        """Save a plot as <name><suffix>, record it for the report (none
        with --lean)."""
        if fig is None or getattr(self.s, 'lean', False):
            return None
        p = save(fig, self.path(suffix))
        self.summary['images'][key] = self.rel(p)
        self.wrote(label, p)
        return p

    # ---- stages -------------------------------------------------------------
    def run(self):
        os.makedirs(self.s.out, exist_ok=True)
        stop.arm(self.s.out)  # the GUI's Stop: a STOP file there (stop.py)
        if self.s.events:
            from .events import EventSink
            os.makedirs(os.path.dirname(os.path.abspath(self.s.events)),
                        exist_ok=True)
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
        # the config's notes in the run's own log (the
        for note in getattr(self.s, 'notes', None) or []:
            # window and a study's runs had not had them)
            self.log(f"config: {note}")
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
        failed = [
            label(x)
            for x in self.named
            if x in self.plan and x not in self.results
        ]
        if failed and not stop.requested():
            self.log(
                f"failed: {', '.join(failed)} gave no result (see the warnings "
                "above)"
            )
        return 1 if failed else 0

    def _drop(self, name, why, what='cannot run'):
        """A solver that cannot take part: False (the run stops) when it is
        named in `solvers`, else it is left out with a warning."""
        if name in self.named:
            self.log(f"{name} {what}: {why}")
            return False
        self.log(f"WARNING: {name} {what}, left out: {why}")
        self.plan.remove(name)
        return True

    def preflight(self):
        """Before any work: can the solvers of this run run? One named in
        `solvers` that cannot stops the run here (False); a reference added by
        --mom / --fast is left out with a warning."""
        s, reg = self.s, registry()
        if LINES in self.plan:  # the line model: qucs if installed, else scikit
            line = resolve_lines(s)
            self.plan = [
                line if x == LINES else x
                for x in self.plan
                if x != line or LINES not in self.plan
            ]
            self.named = [
                line if x == LINES else x
                for x in self.named
                if x != line or LINES not in self.named
            ]
            self.log(
                f"line model: solved with {reg[line].tool} (lines: qucsator if "
                "installed, else scikit-rf)"
            )
        if self.plan:
            self.log("solvers: " + ", ".join(label(x) for x in self.plan))
        for name in [x for x in self.plan if x != AUTO]:
            ok, what = reg[name].check(s)
            if ok:
                continue
            details = reg[name].details()[1] if name in self.named else [what]
            # a longer check: say all of it (openEMS's import)
            if details != [what]:
                self.log(
                    f"{label(name)} cannot run - details:\n"
                    + "\n".join(details) + "\nWithout it: --preview (model, "
                                           "mesh, line sections), or another "
                                           "solver (--check lists them)"
                )
                return False
            if not self._drop(name, what):
                return False
        # a preview prepares them, solves none: warn
        for name in list(self.previewed):
            sv = reg[name]
            # (openEMS's mesh preview needs no openEMS)
            ok, what = (True, '') if sv.previewed else sv.check(s)
            if not ok:
                self.log(f"WARNING: {name} cannot run, left out: {what}")
                self.previewed.remove(name)
        self.summary['solvers'] = list(self.plan)
        return True

    def _part_ports_setup(self):
        """--part-ports (embed.py, R-E5): every part port excited (a run that
        names its excitations gets them added), --part-file and --part-value
        checked against the part ports."""
        from . import embed
        s, m = self.s, self.m
        part_of = getattr(m, 'part_of', None) or {}
        files, values = (embed.pairs(getattr(s, 'part_file', None)),
                         embed.pairs(getattr(s, 'part_value', None)))
        if (files or values) and not part_of:
            raise ValueError(
                "--part-file and --part-value need part ports (--part-ports)")
        for ref in files:
            if ref not in part_of:
                raise ValueError(
                    f"--part-file {ref}: not a part port "
                    f"({', '.join(part_of) or 'none'})"
                )
        self._part_files = {k: os.path.abspath(v) for k, v in files.items()}
        for ref, path in self._part_files.items():
            if not os.path.isfile(path):
                raise ValueError(f"--part-file {ref}: {path} not found")
        self._sweep = embed.sweep(values, part_of)
        if part_of:
            exc = getattr(s, 'excite', None)
            if exc:
                s.excite = list(exc) + [r for r in part_of if r not in exc]
            self.log(
                f"part ports: {', '.join(part_of)}, each closed by its value "
                "after the solve"
                + (f"; {len(self._sweep)} value combination(s) swept"
                   if self._sweep else ''))

    def model(self):
        s = self.s
        self.m = m = board_from_settings(s, log=self.log)
        self.log(m.describe())
        self._part_ports_setup()
        from . import finish
        self.finish = fin = finish.resolve(s, m)
        for n in (fin.notes if fin is not None else []):
            self.log(n)
        bj = self.path('_board.json')
        same = (getattr(s, 'lean', False) and os.path.isfile(str(s.board))
                and os.path.isfile(bj) and os.path.samefile(s.board, bj))
        # (a lean run of that very file, a segment piece's: it is the model's
        # source already; written again it was 10 s of a Thor window's run,
        # the whole board's primitives in each piece)
        self.wrote('board (what every solver gets)',
                   bj if same else m.save(bj))
        ob = m.outline.bounds
        self.summary['model'] = dict(
            board=s.board, outline_mm=[ob[2] - ob[0], ob[3] - ob[1]],
            nets=s.nets or 'all', diel=getattr(s, 'diel', None),
            vias=len(m.vias), vias_signal=int(sum(m.via_signal)),
            signal_nets=sorted(m.signal_nets), stack=[
                dict(name=l['name'], z0=l['z0'], z1=l['z1'], er=l['er'],
                     tand=l['tand']) for l in m.layers[::-1]
            ], copper=[
                dict(layer=n, z=m.cu_z[n], modelled=n in m.copper)
                for n in m.cu_order[::-1]
            ], ports=[
                dict(name=p.name, kind=p.kind, layer=p.layer,
                     ref_layer=p.ref_layer, w=p.w, z0=p.z0,
                     h=abs(p.z_sig - p.z_ref),
                     at=[float(p.c[0]), float(-p.c[1])], direction=p.direction,
                     gap=((p.gap[1] - p.gap[0]) if p.kind == 'gap' else None))
                for p in m.ports
            ], parts=[
                dict(ref=p.ref, value=p.value_str(), layer=p.layer, gap=p.gap,
                     elements=len(p.pieces())) for p in m.parts
            ], notes=list(m.notes), finish=fin.name if fin else None)

    def _lines_needed(self):
        """Whether a solver or a step of the run uses the line model: the
        line solvers, an FDTD solver, de-embedding."""
        sv = self.s.solvers
        names = {x.strip() for x in (sv.split(',') if isinstance(sv, str)
                                     else sv or [])}
        return bool(self.s.deembed or self.s.deembed_file
                    or names - {'mom25d', 'mom'})

    def lines(self):
        """The transmission-line model: sections, coupled groups,
        discontinuities (and their plot). A ballpark aid and what the line
        solvers solve: whatever goes wrong here is a warning, never the end of
        the run."""
        if getattr(self.s, 'lean', False) and not self._lines_needed():
            self.lm = None
            return
        try:
            from .lines.builder import build
            self.lm = lm = build(self.m, field=getattr(self.s, 'lines_field',
                                                       None))
            if not lm.paths:
                self.log("transmission lines: no routed path from the ports" +
                         (f" ({'; '.join(lm.notes)})" if lm.notes else ''))
                return
            self.summary['lines'] = lm.summary()
            # the sections' geometry: the GUI's overlay
            out = self.path('_lines.json')
            with open(out, 'w', encoding='utf-8') as fh:
                json.dump(lm.drawing(getattr(self.m, 'cu_z', None)), fh)
            self.wrote('line model sections (JSON)', out)
            for p, q, secs, branches in lm.paths:
                zs = [e.z0 for e, _, _ in secs if e.z0]
                self.log(
                    f"transmission line {p} -> {q or 'end'}: {len(secs)} "
                    f"sections, {sum(e.length for e, _, _ in secs):.2f} mm"
                    + (f", Z0 {min(zs):.1f}..{max(zs):.1f} ohm" if zs else '')
                    + (f", {len(branches)} branch(es)" if branches else ''))
            if lm.groups or lm.junctions:
                cnt = Counter(j['kind'] for j in lm.junctions)
                self.log(f"  {len(lm.groups)} coupled section(s)"
                         + (" (" + ", ".join(
                             f"{len(g['members'])} lines {g['length']:.2f} mm"
                             for g in lm.groups[:6])
                            + (", ..." if len(lm.groups) > 6 else "")
                            + ")" if lm.groups else "") + "; discontinuities: "
                         + (", ".join(
                             f"{n} {k}"
                             for k, n in sorted(cnt.items())) or "none"))
            self.figure(plot_lines(lm, self.m, self.name), '_tlines.png',
                        'lines', 'transmission-line sections')
        except Exception as e:
            self.log(
                f"WARNING: transmission-line model failed: {type(e).__name__}: "
                f"{e}"
            )
            self.lm = None
            self.summary.pop('lines', None)

    def choose(self):
        """--solvers auto: the full-wave solver (consumes the board) estimated
        cheapest among those that can run, can model this board and fit in
        memory, plus the line model where it applies (only the line model when a
        full-wave solver is named too). False: auto was named and nothing can
        run."""
        if AUTO not in self.plan:
            return True
        s, reg, ram = self.s, registry(), ram_gb()
        est, not_used = {}, {}
        # one named: auto adds the line model
        named_fw = any(n in reg and reg[n].consumes == 'board'
                       for n in self.named)
        for n, sv in reg.items():
            if sv.consumes != 'board' or named_fw or not sv.in_auto:
                continue
            # a preview only prepares it
            ok, what = (sv.check(s) if not (s.preview and sv.previewed) else
                        (True, ''))
            why = sv.assess(self) if ok else [f"cannot run: {what}"]
            if not why:
                try:
                    e = sv.estimate(self)
                except Exception as ex:  # an estimate never stops the run
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
            return self._drop(
                AUTO, ('; '.join(f"{n}: {w}" for n, w in not_used.items())
                       or 'no solver applies'))
        t = lambda sec: f"~{sec:.0f} s" if sec < 120 else f"~{sec / 60:.0f} min"
        self.log(f"solvers auto: {' + '.join(pick)}"
                 + (f" ({est[pick[0]]['what']}, {t(est[pick[0]]['seconds'])})"
                    if pick[0] in est else '')
                 + "".join(f"; {n} not chosen ({e['what']}, {t(e['seconds'])})"
                           for n, e in est.items()
                           if n not in pick)
                 + "".join(f"; {n} not used: {w}" for n, w in not_used.items())
                 + "; force one with --solvers <name>")
        if s.preview:  # a preview solves no full-wave solver
            if pick[0] in est:
                self.log(
                    f"--preview: {pick[0]} would run (without --preview); the "
                    "preview has the line model and openEMS's mesh")
            pick = [p for p in pick if reg[p].consumes != 'board']
            self.previewed += ([
                n for n, sv in reg.items() if sv.previewed and n not in not_used
            ][:1])
        swap = lambda xs: [x for x in xs
                           if x != AUTO] + [p for p in pick if p not in xs
                                            ] if AUTO in xs else xs
        self.plan, self.named = swap(self.plan), swap(self.named)
        self.plan = [n for n in reg if n in self.plan]
        self.summary.update(
            solvers=list(self.plan),
            auto=dict(chosen=pick, not_used=not_used,
                      estimates={n: dict(e)
                                 for n, e in est.items()}))
        return True

    def _assess(self, name):
        """A solver's reasons it cannot model this run: its own, and part ports
        where it has no gap ports."""
        sv = registry()[name]
        why = list(sv.assess(self) or [])
        if (getattr(self.m, 'part_of', None) and str(
                sv.capabilities.get('gap_ports', 'no')).startswith('no')):
            why.append(
                "part ports (--part-ports) are gap ports, which it does not "
                "model"
            )
        return why

    def assess(self):
        """Can each solver model this board in this run? (False: a named one
        cannot; the run stops.)"""
        for name in list(self.plan):
            why = self._assess(name)
            if (why and not self._drop(name, '; '.join(why),
                                       'cannot model this board')):
                return False
        for name in list(self.previewed):
            why = self._assess(name)
            if why:
                self.log(
                    f"WARNING: {name} cannot model this board, left out: "
                    f"{'; '.join(why)}"
                )
                self.previewed.remove(name)
        self.summary['solvers'] = list(self.plan)
        return True

    def estimate(self):
        """--preview: what each full-wave solver would cost on this board
        (Solver.estimate: time, host memory and, mom25d, GPU memory), whether it
        is in the run or not, logged and kept as summary['estimates'] (the GUI's
        System panel shows them against the memory there is)."""
        s, out = self.s, {}
        for n, sv in registry().items():
            if (sv.consumes != 'board' or not (sv.check(s)[0] or sv.previewed)
                    or sv.assess(self)):
                continue
            try:
                e = sv.estimate(self)
            except Exception as ex:  # an estimate never stops a preview
                self.log(f"estimate: {n}: none ({type(ex).__name__}: {ex})")
                continue
            if e is None:
                continue
            out[n] = e = {k: v for k, v in dict(e).items() if v is not None}
            t = (f"~{e['seconds']:.0f} s"
                 if e['seconds'] < 120 else f"~{e['seconds'] / 60:.0f} min")
            self.log(f"estimate: {n}: {e['what']}, {t}, ~{e['gb']:.1f} GB"
                     + (f" + {e['gpu_gb']:.1f} GB on the GPU"
                        if e.get('gpu_gb') else '')
                     + (f" ({e['how']})" if e.get('how') else ''))
        self.summary['estimates'] = dict(solvers=out, ram_gb=ram_gb())
        return out

    def prepare(self):
        if self.s.preview:
            self.estimate()
        names = [n for n in registry() if n in self.plan + self.previewed]
        self._unprepared = set()
        for name in names:
            sv = registry()[name]
            # one solver's set-up failing is a warning, as its solve's is (it
            # had lost the whole run: no run.json, no report)
            try:
                self.prep[name] = sv.prepare(self)
                self.deviations[name] = devs = sv.deviations(
                    self, self.prep[name])
            except stop.Stopped:
                raise
            except Exception as e:
                if os.environ.get('KICAD2EMS_TRACEBACK'):
                    raise
                self.log(
                    f"WARNING: {name} failed in its set-up: "
                    f"{type(e).__name__}: {e}"
                )
                self._unprepared.add(name)
                continue
            if devs:
                self.log(f"{name} changes: " + "; ".join(describe(devs)))
        self.summary['deviations'] = {
            n: [dataclasses.asdict(d)
                for d in ds]
            for n, ds in self.deviations.items()
        }
        self.summary['saw'] = saw_table(self.m, self.s, names, self.deviations)
        # no mesh preview: the model alone
        if (not getattr(self.s, 'lean', False) and not any(
                self.summary['images'].get(k) for k in ('mesh', 'model'))):
            self.figure(preview(self.m, None, self.name), '_model.png', 'model',
                        'model')

    def solve(self):
        """Each solver of the plan, the cheap ones first; --solver-jobs N runs N
        at a time (threads: each solver's files are its own, the log is shared).
        A failure is a warning here; when the solver was named in `solvers`, the
        run ends with exit code 1 (see _run)."""
        from . import reuse
        order = sorted(self.plan, key=lambda n: registry()[n].rank)
        todo = []
        for name in order:
            if name in getattr(self, '_unprepared', ()):
                continue
            sv = registry()[name]  # (R-D20: a run done again)
            key = (None if self.s.no_reuse or not sv.reusable(self) else
                   reuse.key(self, name))
            kept = reuse.load(self, name, key) if key else None
            if kept is not None:
                self.log(
                    f"{label(name)}: the result of an earlier run here, solved "
                    f"{kept.info['reused']['solved']} (the same board, "
                    "frequencies, settings and code; --no-reuse solves it "
                    "again)"
                )
                self.results[name] = self._close_parts(name, kept)
                continue
            todo.append((name, key))

        def one(name, key):
            if stop.requested():
                self.log(f"stopped: {label(name)} not run")
                return
            try:
                r = registry()[name].solve(self, self.prep.get(name))
                if key:
                    reuse.keep(self, name, key, r)
                self.results[name] = self._close_parts(name, r)
            except stop.Stopped:
                self.log(f"stopped: {label(name)} ended early, no result")
            except Exception as e:
                if os.environ.get('KICAD2EMS_TRACEBACK'):
                    raise
                self.log(f"WARNING: {name} failed: {type(e).__name__}: {e}")

        jobs = min(max(1, int(getattr(self.s, 'solver_jobs', 1) or 1)),
                   len(todo))
        if jobs <= 1:
            for name, key in todo:
                one(name, key)
            return
        self.log(
            f"solvers side by side, {jobs} at a time: "
            f"{', '.join(label(n) for n, _ in todo)}"
        )
        from concurrent.futures import ThreadPoolExecutor
        echo, term = self.echo, _terminal_copy() if self.echo is print else None
        # openEMS (in this process) sends descriptors 1 and 2 to its own log
        # while it steps: the others' lines would go there too
        if term:
            lock = threading.Lock()
            def to_term(text):
                with lock:
                    print(text, file=term, flush=True)
            self.echo = to_term
        try:
            with ThreadPoolExecutor(jobs, thread_name_prefix='solver') as ex:
                for f in [ex.submit(one, n, k) for n, k in todo]:
                    f.result()
        finally:
            self.echo = echo
            if term:
                term.close()

    def _part_outputs(self, n, entry):
        """<name>_<n>_ports.sMp (over the external and part ports) and each
        value sweep's closed result."""
        from . import embed
        full, part_of = self.full[n], self.m.part_of
        out = self.path(f"_{n}_ports.s{len(full.names)}p")
        write_touchstone(
            out, self.freq, np.nan_to_num(full.S), full.zref, names=full.names,
            comment=full.comment
            + '; the part ports open, for embedding (kicad2ems.core.embed)')
        self.wrote(f"S-parameters with the part ports, {registry()[n].label}",
                   out)
        entry['part_ports'] = dict(touchstone=self.rel(out),
                                   parts=list(part_of), sweep=[])
        for combo in getattr(self, '_sweep', None) or []:
            loads = embed.loads_of(part_of, self.freq, self._part_files, combo)
            S, names, zref = embed.close(full.S, list(full.names),
                                         list(full.zref), loads)
            f = self.path(f"_{n}_{embed.tag(combo)}.s{len(names)}p")
            write_touchstone(
                f, self.freq, np.nan_to_num(S), zref, names=names,
                comment=full.comment
                + f"; part ports closed with {embed.tag(combo)}")
            entry['part_ports']['sweep'].append(
                dict(values={r: v
                             for r, v, _, _ in combo}, touchstone=self.rel(f),
                     table=sparam_table(self.freq, S, names)))
        if entry['part_ports']['sweep']:
            self.log(
                f"{label(n)}: {len(entry['part_ports']['sweep'])} part value "
                "combination(s) written, no solve each")

    def _close_parts(self, name, r):
        """Solver `name`'s result r over the external and part ports, closed by
        the parts' values (embed.close): the board with its parts, what a run
        without --part-ports gives. The full one is kept (self.full[name]) for
        outputs: its Touchstone file and the value sweeps."""
        from . import embed
        from .solvers import Result
        part_of = getattr(self.m, 'part_of', None) or {}
        if not part_of or not all(k in r.names for k in part_of):
            return r
        self.full[name] = r
        loads = embed.loads_of(part_of, self.freq, self._part_files)
        S, names, zref = embed.close(r.S, list(r.names), list(r.zref), loads)
        info = dict(r.info, part_ports=list(part_of),
                    part_files=dict(self._part_files))
        return Result(
            S, names, zref, r.seconds, info=info, comment=r.comment
            + f"; part ports {', '.join(part_of)} closed by their values")

    def _deembed(self, n, r, entry):
        """<name>_<n>_deembedded.sNp of solver n's result r, its ports' planes
        moved and fixtures removed (deembed.py); a warning where it cannot be
        done, the solver's own S standing."""
        from . import deembed
        pairs = lambda items: dict(
            x.split('=', 1) for x in (items or []) if '=' in x)
        try:
            lines = {
                k.strip(): float(v)
                for k, v in pairs(self.s.deembed).items()
            }
            files = {
                k.strip(): os.path.abspath(v.strip())
                for k, v in pairs(self.s.deembed_file).items()
            }
            S, done = deembed.apply(r.S, r.names, r.zref, self.freq, lines,
                                    files, getattr(self, 'lm', None),
                                    sigma=getattr(self.s, 'sigma', None))
        except (ValueError, OSError) as e:
            self.log(f"WARNING: {label(n)}: not de-embedded: {e}")
            return
        out = self.path(f"_{n}_deembedded.s{len(r.names)}p")
        write_touchstone(
            out, self.freq, np.nan_to_num(S), r.zref, names=r.names,
            comment=r.comment + '; de-embedded: ' + '; '.join(done))
        self.wrote(f"S-parameters de-embedded, {registry()[n].label}", out)
        entry['deembedded'] = dict(touchstone=self.rel(out), done=done,
                                   table=sparam_table(self.freq, S, r.names))
        entry['deembedded']['causality'], warn = causality_note(
            self.freq, S, r.names, f"{label(n)} de-embedded")
        if warn:  # (a plane moved past the true one shows here)
            self.log(warn)
        self.log(f"{label(n)}: de-embedded ({'; '.join(done)}) -> {out}")

    def outputs(self):
        s, freq, reg = self.s, self.freq, registry()
        # registry order: the first is the reference
        done = [n for n in self.plan if n in self.results]
        if not done:
            return
        ref = self.results[done[0]]
        for n in done:
            r = self.results[n]
            out = self.path(f"_{n}.s{len(r.names)}p")
            write_touchstone(out, freq, np.nan_to_num(r.S), r.zref,
                             names=r.names,
                             comment=r.comment + unknown_note(r.S, r.names))
            self.wrote(f"S-parameters, {reg[n].label}", out)
            entry = dict(r.info, label=reg[n].label, touchstone=self.rel(out),
                         seconds=r.seconds, names=r.names, zref=r.zref,
                         table=sparam_table(freq, r.S, r.names))
            if n != done[0] and r.names == ref.names:
                entry.update(reference=done[0],
                             compare=compare_table(freq, ref.S, r.S, r.names))
            entry['passivity'] = pas = passivity(freq, r.S)
            if pas and pas['max'] > 1 + PASSIVE_TOL:  # (nothing had checked it)
                self.log(
                    f"WARNING: {reg[n].label}: not passive at {pas['count']} "
                    f"of {len(freq)} frequencies: its S gives out up to "
                    f"{100 * (pas['max'] ** 2 - 1):.2g} % more power than goes "
                    f"in (largest singular value {pas['max']:.4f} at "
                    f"{pas['f'] / 1e9:.4g} GHz): the mesh, the ports or the "
                    "sweep's fit there; a circuit or time-domain use of it "
                    "needs it passive"
                    +
                    ("" if s.passive else " (--passive writes a passive copy)"))
                if s.passive:  # (R-E2: nothing had made one)
                    P, k = make_passive(r.S)
                    pout = self.path(f"_{n}_passive.s{len(r.names)}p")
                    write_touchstone(
                        pout, freq, np.nan_to_num(P), r.zref, names=r.names,
                        comment=r.comment +
                        f"; made passive: singular values clipped to 1 at {k} "
                        f"frequencies" + unknown_note(r.S, r.names))
                    self.wrote(f"S-parameters made passive, {reg[n].label}",
                               pout)
                    entry['passive_touchstone'] = self.rel(pout)
            # (R-E2)
            entry['causality'], warn = causality_note(freq, r.S, r.names,
                                                      reg[n].label)
            if warn:
                self.log(warn)
            # (R-E1: lumped ports had no de-embedding)
            if s.deembed or s.deembed_file:
                self._deembed(n, r, entry)
            if n in self.full:  # (R-E5: part ports)
                self._part_outputs(n, entry)
            self.summary['solved'][n] = entry
            self.log(f"{reg[n].label}: -> {out}")
        self.join_bands()
        overlays = ([(*read_touchstone(s.compare), os.path.basename(s.compare),
                      '--')] if s.compare else [])
        others = [n for n in done[1:] if self.results[n].names == ref.names]
        overlays += [(freq, self.results[n].S, reg[n].label,
                      OVERLAY_STYLES[i % len(OVERLAY_STYLES)])
                     for i, n in enumerate(others)]
        if (getattr(self, '_joined', None) is not None and ref.names
                == self.results[self.summary['joined']['low']].names):
            overlays.append((freq, self._joined, 'joined',
                             OVERLAY_STYLES[len(others) % len(OVERLAY_STYLES)]))
        if not getattr(s, 'lean', False):
            self.figure(
                plot_results(freq, ref.S, ref.names,
                             f"{self.name} - {reg[done[0]].label}", overlays,
                             label=reg[done[0]].label), f"_{done[0]}.png",
                'sparams', 'S-parameter plot')
        self.summary['results'] = dict(
            reference=done[0], names=ref.names, zref=ref.zref,
            excited=ref.info.get('excited', ref.names), freq_ghz=[
                freq[0] / 1e9, freq[len(freq) // 2] / 1e9, freq[-1] / 1e9
            ], table=self.summary['solved'][done[0]]['table'],
            compare_file=s.compare)
        for n in done:
            # (its solve's own state is not there)
            if 'reused' in self.results[n].info:
                continue
            try:  # a solver's extra outputs (maps, dumps): a
                # warning, the S-parameters stand
                reg[n].outputs(self, self.prep.get(n), self.results[n])
            except stop.Stopped:
                raise
            except Exception as e:
                if os.environ.get('KICAD2EMS_TRACEBACK'):
                    raise
                self.log(
                    f"WARNING: {n}'s outputs failed: {type(e).__name__}: {e}")

    def join_bands(self):
        """A quasi-static solver's answer and a full-wave one's, joined into one
        band at the frequency the first is valid to (join.py):
        <name>_joined.sNp, and summary['joined']."""
        from .join import pick, join, best_crossover
        got = pick(self.results, registry())
        if got is None:
            return
        low, high, said = got
        a, b = self.results[low], self.results[high]
        if (not np.allclose(np.asarray(a.zref, float), np.asarray(
                b.zref, float), rtol=1e-6)):
            self.log(
                f"not joined: {label(low)} and {label(high)} have different "
                "reference impedances"
            )
            return
        freq = self.freq
        if said >= freq[-1] or said <= freq[0]:
            self.log(
                f"not joined: {label(low)} is valid to about {said / 1e6:.3g} "
                "MHz, "
                + ("above the whole band" if said >=
                   freq[-1] else "below the whole band"))
            return
        # where the two agree best, at or below its estimate
        fc, _ = best_crossover(freq, a.S, b.S, said)
        S, info = join(freq, a.S, b.S, fc)
        # the quasi-static solver's own valid_to_Hz
        info['estimate_Hz'] = float(said)
        out = self.path(f"_joined.s{len(a.names)}p")
        write_touchstone(
            out, freq, np.nan_to_num(S), a.zref, names=a.names, comment=
            f"joined: {label(low)} to {fc / 1e6:.4g} MHz, {label(high)} above "
            f"(a raised-cosine blend from {info['blend_Hz'][0] / 1e6:.4g} to "
            f"{info['blend_Hz'][1] / 1e6:.4g} MHz)")
        self.wrote(
            f"S-parameters, {label(low)} below {fc / 1e6:.3g} MHz and "
            f"{label(high)} above",
            out)
        mis = info['mismatch']
        self.summary['joined'] = dict(info, low=low, high=high,
                                      touchstone=self.rel(out),
                                      table=sparam_table(freq, S, a.names))
        self._joined = S
        self.log(
            f"joined: {label(low)} to {fc / 1e6:.3g} MHz, {label(high)} above"
            + (f" (where they agree best: {label(low)} says it holds to "
               f"{said / 1e6:.3g} MHz)" if fc < said else '')
            + (f"; they differ by up to {mis:.3f} in |S| across the "
               "crossover" if mis is not None else '')
            + f" -> {out}")
        if mis is not None and mis > 0.1:
            self.log(
                f"WARNING: {label(low)} and {label(high)} differ by {mis:.2f} "
                "in |S| where they are joined: one of them is off there (the "
                "mesh, a port), and the joined band is no better than the "
                "worse"
            )

    def report(self):
        """<name>_run.json (the run summary) and <name>_report.html (unless
        --no-report)."""
        js = self.path('_run.json')
        self.wrote('run summary (JSON)', js)
        if not self.s.no_report:
            self.wrote('report', self.path('_report.html'))
        sm = self.summary
        sm.update(warnings=self.warnings, log=list(self.messages),
                  runtime_s=time.time() - self.t0)
        if 'results' in sm:  # the keys older readers use
            sm.update(ports=sm['results']['names'], zref=sm['results']['zref'],
                      excited=sm['results']['excited'],
                      parts=sm['model']['parts'],
                      diel=getattr(self.s, 'diel', None))
        with open(js, 'w', encoding='utf-8') as fh:
            json.dump(stamp(sm, 'kicad2ems-run'), fh, indent=1, default=plain)
        if not self.s.no_report:
            from .report import write_report
            write_report(sm, self.path('_report.html'))
            self.log(f"wrote {self.path('_report.html')}")
        self.log(f"done ({time.time() - self.t0:.0f} s)")
