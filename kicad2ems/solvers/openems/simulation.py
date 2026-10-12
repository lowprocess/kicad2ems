"""
openEMS setup and runs: CSX geometry (dielectrics, copper sheets, via cylinders), lumped ports,
excitation, boundaries; one FDTD run per excited port. Needs the openEMS / CSXCAD Python
packages (imported lazily, so everything else works without them).
"""
import glob, json, math, os, threading, time
import numpy as np
from ...constants import EPS0
from ...geometry import split_holes


def _f(v):
    """Plain Python float: older CSXCAD / openEMS releases reject numpy scalars (type checks)."""
    return float(v)


def _fl(v):
    """List of plain floats (coordinates)."""
    return [float(x) for x in v]


def build_fdtd(m, mesh, fmin, fmax, excite, bc=('MUR',) * 6, end_db=-40, sigma=None, max_steps=1_000_000,
               dumps=(), diel='fixed', diel_fref=1e9, exc_fmax=None, api=None):
    """diel: 'fixed' (loss as a conductivity fitted at the band centre) or 'wideband' (Djordjevic-Sarkar
    model matched at diel_fref Hz, as Debye poles; see dielectric.py). exc_fmax: upper edge of the
    Gaussian excitation (default fmax; antenna runs extend it, see antenna.py). api: (ContinuousStructure,
    openEMS) classes to build with instead of CSXCAD's and openEMS's (readback.py records the calls)."""
    if api is None:
        from CSXCAD import ContinuousStructure
        from openEMS import openEMS
    else:
        ContinuousStructure, openEMS = api
    FDTD = openEMS(NrTS=int(max_steps), EndCriteria=_f(10 ** (end_db / 10)))
    fe = fmax if exc_fmax is None else exc_fmax
    f0, fc = _f(0.5 * (fmin + fe)), _f(0.5 * (fe - fmin) * 1.05)
    FDTD.SetGaussExcite(f0, fc)
    FDTD.SetBoundaryCond([str(b) for b in bc])
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    grid = CSX.GetGrid()
    grid.SetDeltaUnit(1e-3)
    grid.SetLines('x', np.asarray(mesh['x'], float)); grid.SetLines('y', np.asarray(mesh['y'], float))
    grid.SetLines('z', np.asarray(mesh['z'], float))
    lines = [np.asarray(mesh[a], float) for a in 'xyz']

    def on_mesh(s, e, tol=1e-5):
        """A box's flat sides (start = stop on an axis) moved exactly onto the nearest mesh line: openEMS
        drops a zero-thickness port or element that misses its line. Two differential legs whose port planes
        differed by 1e-12 mm got one merged line, and the second leg's excitation found no edges."""
        s, e = list(s), list(e)
        for a in range(3):
            if abs(s[a] - e[a]) < 1e-9 and len(lines[a]):
                near = lines[a][np.argmin(np.abs(lines[a] - s[a]))]
                if abs(near - s[a]) < tol:
                    s[a] = e[a] = float(near)
        return s, e
    ox, oy = m.outline.exterior.xy
    pts = [np.asarray(ox[:-1], float), np.asarray(oy[:-1], float)]
    for l in m.layers:
        name = l['name'].replace(' ', '_')
        if diel == 'wideband' and l['tand'] > 0:
            mat = _debye_material(CSX, name, l, diel_fref, fmin, fmax, mesh['info']['dt_ps'] * 1e-12)
        else:                                                       # loss at the centre frequency
            kappa = _f(2 * np.pi * f0 * EPS0 * l['er'] * l['tand'])
            mat = CSX.AddMaterial(name, epsilon=_f(l['er']), kappa=kappa)
        mat.AddLinPoly(pts, 'z', _f(l['z0']), _f(l['z1'] - l['z0']), priority=1)
    for lay, ps in m.copper.items():
        if sigma:
            metal = CSX.AddConductingSheet(f"cu_{lay}", conductivity=_f(sigma), thickness=_f(m.cu_t[lay] * 1e-3))
        else:
            metal = CSX.AddMetal(f"cu_{lay}")
        for p in ps:
            for q in split_holes(p):
                x, y = q.exterior.xy
                metal.AddPolygon([np.asarray(x[:-1], float), np.asarray(y[:-1], float)], 'z', _f(m.cu_z[lay]),
                                 priority=10)
    vias = mesh.get('vias', m.vias)                                 # as placed by the mesher (snapped)
    if vias:
        vm = CSX.AddMetal('vias')
        for (x, y, r, z0, z1) in vias:
            vm.AddCylinder(_fl([x, y, z0]), _fl([x, y, z1]), _f(r), priority=11)
    walls = mesh.get('walls') or []                                # --edge-walls: PEC sheets where the planes end
    if walls:
        wm = CSX.AddMetal('edge_walls')
        for axis, c, t0, t1, z0, z1 in walls:
            s, e = ([c, t0, z0], [c, t1, z1]) if axis == 'x' else ([t0, c, z0], [t1, c, z1])
            wm.AddBox(_fl(s), _fl(e), priority=11)
    for pt in getattr(m, 'parts', []):                              # lumped R / L / C between two pads
        s, e = pt.box()
        if pt.short:
            CSX.AddMetal(f"part_{pt.ref}").AddBox(_fl(s), _fl(e), priority=12)
            continue
        for suffix, vals, series, ps, pe in pt.pieces():
            ps, pe = on_mesh(ps, pe)
            kw = {k: _f(v) for k, v in vals.items()}
            if series:
                kw['LEtype'] = 1
            try:
                el = CSX.AddLumpedElement(f"part_{pt.ref}{suffix}", ny=int(pt.axis), caps=True, **kw)
            except Exception as ex:                                 # older CSXCAD: no L / LEtype
                raise RuntimeError(f"{pt.ref}: this CSXCAD cannot make a lumped element with {sorted(kw)} ({ex}) - "
                                   f"update openEMS, or skip the part (--parts none / Sim.Enable = 0)") from ex
            el.AddBox(_fl(ps), _fl(pe), priority=12)
    ports = []
    for i, p in enumerate(m.ports):
        s, e = on_mesh(*p.box())
        ports.append(FDTD.AddLumpedPort(i + 1, _f(p.z0), _fl(s), _fl(e), getattr(p, 'direction', 'z'),
                                        excite=1.0 if i == excite else 0.0, priority=12))
    if dumps:
        from .dumps import add_dumps
        add_dumps(CSX, dumps)
    return FDTD, CSX, ports


def _debye_material(CSX, name, layer, fref, fmin, fmax, dt):
    """Debye-pole material for one dielectric (openEMS skips relaxation times below 2 time steps, so
    the fit only uses poles above 2.5 dt)."""
    from CSXCAD.CSProperties import CSPropDebyeMaterial
    from .dielectric import fit_debye
    e_inf, poles, kappa = fit_debye(layer['er'], layer['tand'], fref, fmin, fmax, tau_min=2.5 * dt)
    if not poles:
        return CSX.AddMaterial(name, epsilon=_f(e_inf), kappa=_f(kappa))
    mat = CSPropDebyeMaterial(CSX.GetParameterSet(), order=len(poles), name=name, epsilon=_f(e_inf), kappa=_f(kappa))
    CSX.AddProperty(mat)
    for i, (d, tau) in enumerate(poles):
        mat.SetDispersiveMaterialProperty(order=i, eps_delta=_f(d), eps_relax=_f(tau))
    return mat


def check_openems():
    """Can this Python import openEMS? Returns (ok, report lines) with the real errors and hints."""
    import importlib, shutil, sys
    lines = [f"python      {sys.executable}",
             f"environment {sys.prefix}" + ("  (virtual env)" if sys.prefix != getattr(sys, 'base_prefix', sys.prefix) else "  (no virtual env active)"),
             f"LD_LIBRARY_PATH={os.environ.get('LD_LIBRARY_PATH', '')}"]
    ok, hints = True, []
    for mod in ('numpy', 'h5py', 'CSXCAD', 'openEMS'):
        try:
            m = importlib.import_module(mod)
            lines.append(f"  ok      {mod:8s} {getattr(m, '__file__', '')}")
        except Exception as e:                                  # ImportError, OSError from the .so, ...
            ok = False
            msg = f"{type(e).__name__}: {e}"
            lines.append(f"  FAILED  {mod:8s} {msg}")
            if isinstance(e, ModuleNotFoundError) and e.name == mod and mod in ('CSXCAD', 'openEMS'):
                hints.append(f"{mod} is not installed for THIS python ({sys.executable}). If you installed it in a "
                             f"venv, run kicad2ems from that venv (python -m kicad2ems ...), or pip-install "
                             f"the {mod} python package (openEMS-Project/{mod}/python) with this python.")
            elif isinstance(e, ModuleNotFoundError):
                hints.append(f"missing dependency '{e.name}': python -m pip install {e.name}")
            elif '.so' in str(e) or 'shared object' in str(e) or isinstance(e, OSError):
                hints.append(f"{mod} is installed but cannot load the compiled openEMS/CSXCAD libraries: add the "
                             f"openEMS install's lib folder to LD_LIBRARY_PATH (e.g. export LD_LIBRARY_PATH="
                             f"$HOME/opt/openEMS/lib:$LD_LIBRARY_PATH)")
    exe = shutil.which('kicad2ems')
    if exe:
        try:
            with open(exe) as fh:
                first = fh.readline().strip()
        except (OSError, UnicodeDecodeError):
            first = ''
        launcher = first[2:].strip() if first.startswith('#!') else ''
        lines.append(f"command     {exe} -> {launcher or '?'}")
        # compare the bin folders, not resolved files: a venv's python is a symlink to the system one
        if launcher and os.path.dirname(os.path.abspath(launcher.split()[0])) != os.path.dirname(os.path.abspath(sys.executable)):
            hints.append(f"the 'kicad2ems' command on PATH runs {launcher}, not this python - reinstall it here "
                         f"(python -m pip install <kicad2ems folder>) or use: python -m kicad2ems ...")
    in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
    if not ok and in_venv:
        cfg = os.path.join(sys.prefix, 'pyvenv.cfg')
        try:
            sysite = 'include-system-site-packages = true' in open(cfg).read().replace('True', 'true')
        except OSError:
            sysite = True
        if not sysite:
            hints.append("this venv does not see system packages: if openEMS came from your distribution "
                         "(dnf / apt), recreate the venv with  python3 -m venv --system-site-packages <dir>")
    if hints:
        lines.append("hints:")
        lines += [f"  - {h}" for h in dict.fromkeys(hints)]
    return ok, lines


def port_index(m, names):
    """Indices of single-ended ports to excite: names may be legs (P1+) or whole ports (P1)."""
    if not names:
        return list(range(len(m.ports)))
    want = [s.strip() for s in (names.split(',') if isinstance(names, str) else names)]
    idx = [i for i, p in enumerate(m.ports) if p.name in want or p.pair in want]
    if not idx:
        raise ValueError(f"no port matches {want}; ports: {[p.name for p in m.ports]}")
    return idx


def pulse_steps(fmin, fmax, dt_ps, exc_fmax=None):
    """Length of openEMS's Gaussian excitation in timesteps (2 * 9 / (2 pi fc) / dt, fc as set in build_fdtd),
    with the mesher's time step estimate (openEMS's own step is often ~10 % smaller, so the true count is a
    little higher). The fields must ring down after it: allow --max-steps well above this."""
    fe = fmax if exc_fmax is None else exc_fmax
    fc = 0.5 * (fe - fmin) * 1.05
    return int(np.ceil(2 * 9 / (2 * np.pi * fc) / (dt_ps * 1e-12)))


def _times(fn, n=None):
    """First column of an openEMS text probe file (comment lines start with %); the first n values, or all."""
    out = []
    with open(fn) as fh:
        for line in fh:
            if line.strip() and not line.startswith('%'):
                out.append(float(line.split()[0]))
                if n and len(out) >= n:
                    break
    return out


def steps_taken(path, dt_ps=None):
    """Timesteps a finished run took: the last sample time of its port voltage probe divided by openEMS's
    time step, read from the excitation signal file 'et' (one sample per step; the mesher's estimate dt_ps,
    used only without 'et', can be ~10 % off). The probes are subsampled, so this may undercount by a few
    dozen steps. None if there is no probe file."""
    et = os.path.join(path, 'et')
    dt = None
    if os.path.exists(et):
        t = _times(et, 3)
        if len(t) >= 3 and t[2] > t[1]:
            dt = t[2] - t[1]
    if dt is None and dt_ps:
        dt = dt_ps * 1e-12
    for fn in sorted(glob.glob(os.path.join(path, 'port_ut*'))):
        t = _times(fn)
        if t and dt:
            return int(round(t[-1] / dt))
    return None


def step_limit_warning(name, steps, max_steps, end_db):
    """Warning text when a run stopped at --max-steps instead of decaying to --end-db (None otherwise)."""
    if steps is None or steps < 0.97 * max_steps:
        return None
    return (f"WARNING: {name} ran to the step limit (~{steps} of --max-steps {max_steps}) before the energy fell by "
            f"{-end_db:g} dB - the fields may still be ringing; raise --max-steps (or accept a higher --end-db)")


DEAD_DB = -120.0         # PortWatch: port signals this far below their peak end a run at once


class PortWatch:
    """Thread that ends a run whose field energy has stalled once every port signal has died away.
    openEMS's own end criterion is the total field energy, which can level off above --end-db when some
    energy sits where no port sees it (a static field, a mode the ports do not couple to) while the
    S-parameters are long settled. Every `interval` s it reads the port voltage / current probes
    (port_ut* / port_it*, which openEMS flushes as it runs) and the energy from openEMS's status lines
    (openems.log). It writes ABORT into the run folder (openEMS stops and finishes normally) and records
    why in run_end.json when all of these hold:
      - the time is past t_min (the excitation pulse) and the largest u^2 + (z i)^2 of any port over the
        last `window` s is end_db below its peak;
      - the energy has stalled: over the last quarter of the steps so far (and at least 8 status lines)
        it fell by less than `stall_db`.
    The second condition matters: in a high-Q circuit (a filter) the stored energy leaks out through
    the ports slowly, the port signals sit far below their peak while it does, and stopping then cuts
    the ring-down off (the response comes out smoothed and shifted). Port signals below DEAD_DB end it
    without the stall: nothing reaches the ports any more (a radial stub held the pulse's DC charge at
    -33 dB of energy, its ports at -222 dB, for 176k timesteps where 20k were enough)."""
    def __init__(self, path, end_db, t_min, window, z=50.0, interval=2.0, stall_db=1.5):
        self.path, self.end_db, self.t_min, self.window, self.z, self.interval = path, end_db, t_min, window, z, interval
        self.stall_db, self.energy, self._pos = stall_db, [], 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.fired = None

    def _read_energy(self):
        from .progress import parse_line
        try:
            with open(os.path.join(self.path, 'openems.log'), 'rb') as fh:
                fh.seek(self._pos)
                chunk = fh.read()
        except OSError:
            return
        cut = chunk.rfind(b'\n') + 1
        self._pos += cut
        for line in chunk[:cut].decode('utf-8', 'replace').splitlines():
            p = parse_line(line)
            if p and p['db'] == p['db']:
                self.energy.append((p['step'], p['db']))

    def stalled(self):
        """The energy fell by less than stall_db over the last quarter of the steps (>= 8 status lines)."""
        if len(self.energy) < 8:
            return False
        last = self.energy[-1][0]
        recent = [db for st, db in self.energy if st >= 0.75 * last]
        if len(recent) < 4:
            recent = [db for _, db in self.energy[-4:]]
        return max(recent) - min(recent) < self.stall_db

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def level(self):
        """(time of the last sample s, level of the last window below the peak in dB) or None."""
        return port_decay(self.path, self.window, self.z)

    def _loop(self):
        from ... import stop
        while not self._stop.wait(self.interval):
            if stop.requested():                                # the GUI's Stop (stop.py)
                self.fired = dict(reason='stop')
                open(os.path.join(self.path, 'ABORT'), 'w').close()
                return
            try:
                lv = self.level()
            except Exception:                                   # a half-written file: try again later
                continue
            self._read_energy()
            if lv and lv[0] > self.t_min + self.window and lv[1] <= self.end_db and (self.stalled() or lv[1] <= DEAD_DB):
                self.fired = dict(reason='ports', t_ns=lv[0] * 1e9, level_db=lv[1],
                                  energy_db=self.energy[-1][1] if self.energy else None)
                with open(os.path.join(self.path, 'run_end.json'), 'w') as fh:
                    json.dump(self.fired, fh)
                open(os.path.join(self.path, 'ABORT'), 'w').close()
                return


def port_decay(path, window, z=50.0):
    """(last probe time s, max over ports of the signal power in the last `window` s relative to its peak,
    dB) from the port probe files of a run folder, or None when there are none yet."""
    peak, last, tmax = 0.0, 0.0, None
    for fu in sorted(glob.glob(os.path.join(path, 'port_ut*'))):
        fi = fu.replace('port_ut', 'port_it')
        u = np.loadtxt(fu, comments='%', ndmin=2)
        i = np.loadtxt(fi, comments='%', ndmin=2) if os.path.exists(fi) else None
        n = len(u) if i is None else min(len(u), len(i))
        if n < 4:
            return None
        t = u[:n, 0]
        pw = u[:n, 1] ** 2 + ((z * i[:n, 1]) ** 2 if i is not None else 0.0)
        peak = max(peak, float(pw.max()))
        tmax = t[-1] if tmax is None else min(tmax, t[-1])
        last = max(last, float(pw[t >= t[-1] - window].max()))
    if tmax is None or peak <= 0:
        return None
    return float(tmax), 10 * math.log10(max(last, 1e-300) / peak)


def pulse_time(fmin, fmax):
    """Length (s) of openEMS's Gaussian excitation for the band (as set in build_fdtd)."""
    fc = 0.5 * (fmax - fmin) * 1.05
    return 2 * 9 / (2 * math.pi * fc)


def execute(FDTD, path, threads=0, verbose=1, progress=None, watch=None):
    """FDTD.Run in `path`, restoring the working directory (openEMS changes it). Returns seconds taken.
    progress: None (openEMS writes to the terminal), or dict(mode, label, end_db, max_steps[, on_status]):
    openEMS's output goes to <path>/openems.log and, for mode 'bar' / 'raw', a progress line / the raw
    lines to the terminal ('file': the log only - parallel workers, monitored by the main process);
    on_status(label, status dict) is called for every openEMS status line."""
    from .progress import Monitor, Tracker, redirect_output
    from ... import stop
    stop.check()                                                # stopped: no further excitation or level
    os.makedirs(path, exist_ok=True)
    for f in ('ABORT', 'run_end.json'):                         # leftovers of an earlier run in this folder
        if os.path.exists(os.path.join(path, f)):
            os.remove(os.path.join(path, f))
    cwd, t1 = os.getcwd(), time.time()
    pw = PortWatch(path, **watch).start() if watch else None
    try:
        if progress is None:
            FDTD.Run(path, cleanup=True, numThreads=threads, verbose=verbose)
        else:
            log = log_path(path)
            open(log, 'w').close()
            with redirect_output(log) as terminal:
                mon = None
                if progress['mode'] in ('bar', 'raw'):
                    tr = Tracker(progress['label'], progress['end_db'], progress['max_steps'])
                    mon = Monitor([(progress['label'], log, tr)], progress['mode'], out_fd=terminal,
                                  on_status=progress.get('on_status')).start()
                try:
                    FDTD.Run(path, cleanup=True, numThreads=threads, verbose=max(verbose, 1))
                finally:
                    if mon:
                        mon.stop()
    finally:
        os.chdir(cwd)
        if pw:
            pw.stop()
    if os.path.exists(os.path.join(path, 'ABORT')):
        os.remove(os.path.join(path, 'ABORT'))
    return time.time() - t1


def log_path(path):
    """openEMS's output of a run (written when the run has a progress setting)."""
    return os.path.join(path, 'openems.log')


class RunResult:
    """Result of run_excitations: S (nf, n, n) over the single-ended ports (NaN in the columns of ports not
    excited), dump_files {(port, dump name): path}, dump_norm {port: {freq: incident voltage}} (field maps
    scaled to a 1 V incident wave), seconds / steps {port: value} per run, extra {port: what the run's
    `analyse` hook returned} (antenna mode: the far-field results)."""
    def __init__(self, S):
        self.S, self.dump_files, self.dump_norm, self.seconds, self.steps, self.extra = S, {}, {}, {}, {}, {}
        self.ended = {}                             # {port: dict(reason='ports', t_ns, level_db)} when PortWatch ended it


def run_dir(m, k, out_dir):
    """Folder of the run that excites port k: <out>/openems/exc_<port> (P1+ / P1- -> exc_P1p / exc_P1n)."""
    tag = m.ports[k].name.replace('+', 'p').replace('-', 'n')
    return os.path.abspath(os.path.join(out_dir, 'openems', f"exc_{tag}"))


def _run_port(k, m, mesh, freq, bc, out_dir, end_db=-40, sigma=None, threads=0, xml_path=None, dumps=(),
              verbose=1, diel='fixed', diel_fref=1e9, max_steps=1_000_000, progress=None, exc_fmax=None,
              setup=None, analyse=None, end_on='ports'):
    """One openEMS run with port k excited (top level, so it can run in a worker process). setup(FDTD, CSX)
    may add to the model before the run (antenna mode: the NF2FF box) and its return value is handed to
    analyse(path, ports, token, k, col) after the run. Returns (k, S column (nf, n), {(port, dump): path},
    {freq: incident voltage} or None, seconds, steps taken, what analyse returned or None)."""
    fmin, fmax = freq[0], freq[-1]
    path = run_dir(m, k, out_dir)
    FDTD, CSX, ports = build_fdtd(m, mesh, fmin, fmax, k, bc=bc, end_db=end_db, sigma=sigma, dumps=dumps,
                                  diel=diel, diel_fref=diel_fref, max_steps=max_steps, exc_fmax=exc_fmax)
    token = setup(FDTD, CSX) if setup else None
    os.makedirs(path, exist_ok=True)
    if dumps:
        from .dumps import write_info
        write_info(path, dumps, m, mesh['info'].get('dt_ps'))
    else:                                                       # an earlier run's dumps are gone (openEMS cleans up)
        for f in ('dump_info.json', 'dump_norm.json'):
            if os.path.exists(os.path.join(path, f)):
                os.remove(os.path.join(path, f))
    if xml_path:
        CSX.Write2XML(os.path.abspath(xml_path))
    if progress is not None:
        progress = dict(progress, label=m.ports[k].name, end_db=end_db, max_steps=max_steps)
    watch = None
    if end_on == 'ports':
        fe = fmax if exc_fmax is None else exc_fmax
        watch = dict(end_db=end_db, t_min=pulse_time(fmin, fe), window=max(2.0 / fmin, 1e-9), z=m.ports[k].z0)
    seconds = execute(FDTD, path, threads, verbose, progress, watch=watch)
    files, norm = {}, None
    for d in dumps:
        files[(m.ports[k].name, d.name)] = (os.path.join(path, d.name + '.h5') if d.freqs
                                            else os.path.join(path, d.name + '_*.vtr'))
    fd = sorted({f for d in dumps for f in (d.freqs or [])})
    if fd:                                                      # incident wave at the dump frequencies
        ports[k].CalcPort(path, np.array(fd))
        norm = dict(zip(fd, np.atleast_1d(ports[k].uf_inc)))
        from .dumps import write_norm
        write_norm(path, norm)
    for p in ports:
        p.CalcPort(path, freq)
    if not np.any(np.abs(ports[k].uf_inc) > 0):                 # nothing went in: no S column to compute
        raise RuntimeError(f"port {m.ports[k].name} was not excited (no incident wave: its box catches no mesh "
                           f"edges; see the run's openems.log, 'Voltage excitations')")
    col = np.array([p.uf_ref / ports[k].uf_inc for p in ports]).T
    extra = analyse(path=path, ports=ports, token=token, k=k, col=col) if analyse else None
    return k, col, files, norm, seconds, steps_taken(path, mesh['info']['dt_ps']), extra


def run_excitations(m, mesh, freq, excite, bc, out_dir, end_db=-40, sigma=None, threads=0, xml_path=None,
                    dumps=(), dump_runs=None, jobs=1, diel='fixed', diel_fref=1e9, max_steps=1_000_000, progress=None,
                    exc_fmax=None, setup=None, analyse=None, log=print, end_on='ports'):
    """One openEMS run per excited port, `jobs` of them at a time (separate processes; without `threads`,
    half the logical CPUs are shared between them). Field dumps are added to the runs in dump_runs (port
    indices; default: the first excited port). progress: None or dict(mode='bar' | 'raw'[, on_status]) -
    see execute(). setup / analyse: hooks of every run (see _run_port; picklable for jobs > 1).
    Returns a RunResult."""
    dump_runs = [excite[0]] if dump_runs is None else dump_runs
    n = len(m.ports)
    res = RunResult(np.full((len(freq), n, n), np.nan + 0j))
    jobs = max(1, min(int(jobs or 1), len(excite)))
    if jobs > 1 and not threads:
        # FDTD is memory-bound: half the logical CPUs (no hyper-thread pairs) shared by the runs; giving each
        # run cpu_count / jobs threads oversubscribes the cores and made every run ~4x slower
        threads = max(1, (os.cpu_count() or 2) // 2 // jobs)
    tasks = [dict(k=k, dumps=dumps if k in dump_runs else (), xml_path=xml_path if k == excite[0] else None)
             for k in excite]

    def collect(out):
        k, col, files, norm, seconds, steps, extra = out
        name = m.ports[k].name
        res.S[:, :, k] = col
        res.dump_files.update(files)
        res.seconds[name], res.steps[name] = seconds, steps
        if norm is not None:
            res.dump_norm[name] = norm
        if extra is not None:
            res.extra[name] = extra
        for (pn, dn), path in files.items():
            if not glob.glob(os.path.join(os.path.dirname(path), dn + '*')):
                log(f"  WARNING: no output found for field dump {dn}")
        log(f"  {name} done in {seconds:.0f} s" + (f", ~{steps} timesteps" if steps else ''))
        end = os.path.join(run_dir(m, k, out_dir), 'run_end.json')
        if os.path.exists(end):
            with open(end) as fh:
                res.ended[name] = json.load(fh)
            log(f"  {name}: stopped when the port signals had decayed by {-res.ended[name]['level_db']:.1f} dB "
                f"(at {res.ended[name]['t_ns']:.2f} ns; the field energy had not reached --end-db)")
        w = step_limit_warning(name, steps, max_steps, end_db)
        if w:
            log("  " + w)

    common = dict(m=m, mesh=mesh, freq=freq, bc=bc, out_dir=out_dir, end_db=end_db, sigma=sigma, threads=threads,
                  diel=diel, diel_fref=diel_fref, max_steps=max_steps, exc_fmax=exc_fmax, setup=setup, analyse=analyse,
                  end_on=end_on)
    if jobs == 1:
        for t in tasks:
            log(f"openEMS: exciting {m.ports[t['k']].name} -> {run_dir(m, t['k'], out_dir)}")
            collect(_run_port(**t, **common, progress=progress))
        return res
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from .progress import Monitor, Tracker
    log(f"openEMS: {len(tasks)} excitation(s), {jobs} at a time, {threads} thread(s) each "
        f"(openEMS output in openems.log of each run under {os.path.join(out_dir, 'openems')})")
    mon = None
    if progress is not None:
        for t in tasks:                                             # fresh logs, so the monitor never reads old ones
            os.makedirs(run_dir(m, t['k'], out_dir), exist_ok=True)
            open(log_path(run_dir(m, t['k'], out_dir)), 'w').close()
        mon = Monitor([(m.ports[t['k']].name, log_path(run_dir(m, t['k'], out_dir)),
                        Tracker(m.ports[t['k']].name, end_db, max_steps)) for t in tasks], progress['mode'],
                      on_status=progress.get('on_status')).start()
    try:
        with ProcessPoolExecutor(jobs, mp_context=multiprocessing.get_context('spawn')) as ex:
            futs = [ex.submit(_run_port, **t, **common, verbose=1, progress=dict(mode='file')) for t in tasks]
            outs = [f.result() for f in as_completed(futs)]
    finally:
        if mon:
            mon.stop()
    for out in outs:                                                # after the progress lines: clean output
        collect(out)
    return res
