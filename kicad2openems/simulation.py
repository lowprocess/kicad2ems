"""
openEMS setup and runs: CSX geometry (dielectrics, copper sheets, via cylinders), lumped ports,
excitation, boundaries; one FDTD run per excited port. Needs the openEMS / CSXCAD Python
packages (imported lazily, so everything else works without them).
"""
import os, time
import numpy as np
from .constants import EPS0
from .geometry import split_holes


def _f(v):
    """Plain Python float: older CSXCAD / openEMS releases reject numpy scalars (type checks)."""
    return float(v)


def _fl(v):
    """List of plain floats (coordinates)."""
    return [float(x) for x in v]


def build_fdtd(m, mesh, fmin, fmax, excite, bc=('MUR',) * 6, end_db=-40, sigma=None, max_steps=1_000_000):
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS
    FDTD = openEMS(NrTS=int(max_steps), EndCriteria=_f(10 ** (end_db / 10)))
    f0, fc = _f(0.5 * (fmin + fmax)), _f(0.5 * (fmax - fmin) * 1.05)
    FDTD.SetGaussExcite(f0, fc)
    FDTD.SetBoundaryCond([str(b) for b in bc])
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    grid = CSX.GetGrid()
    grid.SetDeltaUnit(1e-3)
    grid.SetLines('x', np.asarray(mesh['x'], float)); grid.SetLines('y', np.asarray(mesh['y'], float))
    grid.SetLines('z', np.asarray(mesh['z'], float))
    ox, oy = m.outline.exterior.xy
    pts = [np.asarray(ox[:-1], float), np.asarray(oy[:-1], float)]
    for l in m.layers:                                              # dielectrics (loss at the centre frequency)
        kappa = _f(2 * np.pi * f0 * EPS0 * l['er'] * l['tand'])
        mat = CSX.AddMaterial(l['name'].replace(' ', '_'), epsilon=_f(l['er']), kappa=kappa)
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
    ports = []
    for i, p in enumerate(m.ports):
        s, e = p.box()
        ports.append(FDTD.AddLumpedPort(i + 1, _f(p.z0), _fl(s), _fl(e), 'z', excite=1.0 if i == excite else 0.0,
                                        priority=12))
    return FDTD, CSX, ports


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
                             f"venv, run kicad2openems from that venv (python -m kicad2openems ...), or pip-install "
                             f"the {mod} python package (openEMS-Project/{mod}/python) with this python.")
            elif isinstance(e, ModuleNotFoundError):
                hints.append(f"missing dependency '{e.name}': python -m pip install {e.name}")
            elif '.so' in str(e) or 'shared object' in str(e) or isinstance(e, OSError):
                hints.append(f"{mod} is installed but cannot load the compiled openEMS/CSXCAD libraries: add the "
                             f"openEMS install's lib folder to LD_LIBRARY_PATH (e.g. export LD_LIBRARY_PATH="
                             f"$HOME/opt/openEMS/lib:$LD_LIBRARY_PATH)")
    exe = shutil.which('kicad2openems')
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
            hints.append(f"the 'kicad2openems' command on PATH runs {launcher}, not this python - reinstall it here "
                         f"(python -m pip install <kicad2openems folder>) or use: python -m kicad2openems ...")
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


def run_excitations(m, mesh, freq, excite, bc, out_dir, end_db=-40, sigma=None, threads=0, xml_path=None,
                    log=print):
    """One openEMS run per excited port. Returns S (nf, n, n) over the single-ended ports, NaN in the
    columns of ports that were not excited."""
    fmin, fmax = freq[0], freq[-1]
    n = len(m.ports)
    S = np.full((len(freq), n, n), np.nan + 0j)
    for k in excite:
        tag = m.ports[k].name.replace('+', 'p').replace('-', 'n')
        path = os.path.abspath(os.path.join(out_dir, 'openems', f"exc_{tag}"))
        FDTD, CSX, ports = build_fdtd(m, mesh, fmin, fmax, k, bc=bc, end_db=end_db, sigma=sigma)
        os.makedirs(path, exist_ok=True)
        if xml_path and k == excite[0]:
            CSX.Write2XML(os.path.abspath(xml_path))
        log(f"openEMS: exciting {m.ports[k].name} -> {path}")
        cwd, t1 = os.getcwd(), time.time()
        try:
            FDTD.Run(path, cleanup=True, numThreads=threads, verbose=1)
        finally:
            os.chdir(cwd)                                       # openEMS changes the working directory
        for p in ports:
            p.CalcPort(path, freq)
        for i, p in enumerate(ports):
            S[:, i, k] = p.uf_ref / ports[k].uf_inc
        log(f"  done in {time.time() - t1:.0f} s")
    return S
