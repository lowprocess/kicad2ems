"""
EMerge (https://github.com/FennisRobert/EMerge, GPL-2.0-or-later), a 3D finite-element solver in Python, as an
optional, experimental solver: --solvers emerge. It runs in a process of its own (worker.py) with the Python EMerge is
installed in: kicad2ems's own (tools/emerge/install_emerge.sh: EMerge from git, patched for Python 3.14), or
another one (PyPI's EMerge 2.5+ needs Python <= 3.13):

    tools/emerge/install_emerge.sh .venv/bin/python                     # or ./setup.sh: EMerge from git
    python3.13 -m venv ~/emerge-venv && ~/emerge-venv/bin/pip install emerge
    kicad2ems board.toml --solvers emerge --emerge-python ~/emerge-venv/bin/python

EMerge solves `--emerge-points` frequencies spread over the band and fits a rational model through them for
the run's frequencies (its model_S). Its first-order absorbing boundary sits --emerge-margin mm from the board.
"""
import json, os, shutil, subprocess, sys, time

import numpy as np

from ...options import Option
from .. import Solver, Result, Deviation
from ... import stop
from ..openems.boundaries import bottom_is_plane

G = 'EMerge (experimental)'
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'worker.py')
_checked = {}


def emerge_python(s=None):
    """The Python to run EMerge with: --emerge-python, else $KICAD2EMS_EMERGE_PYTHON, else this one."""
    return (getattr(s, 'emerge_python', None) or os.environ.get('KICAD2EMS_EMERGE_PYTHON') or sys.executable)


def emerge_status(python):
    """(usable, one line) of EMerge in that Python (cached)."""
    if python not in _checked:
        exe = shutil.which(python) or python
        try:
            p = subprocess.run([exe, '-c', 'import emerge, sys; print(emerge.__version__, sys.version.split()[0])'],
                               capture_output=True, text=True, timeout=120)
            ver = p.stdout.strip().splitlines()[-1] if p.returncode == 0 and p.stdout.strip() else None
        except (OSError, subprocess.TimeoutExpired) as e:
            ver, p = None, None
        if ver:
            em, py = ver.split()[:2]
            _checked[python] = (True, f"EMerge {em} (Python {py}, {exe})")
        else:
            _checked[python] = (False, f"EMerge is not installed in {exe}: tools/emerge/install_emerge.sh {exe} "
                                       f"(from git, patched for Python 3.14), or a Python <= 3.13 with pip install "
                                       f"emerge and --emerge-python")
    return _checked[python]


class EMerge(Solver):
    name, aliases, label = 'emerge', ('fem',), 'EMerge (FEM, experimental)'
    consumes, previewed, rank = 'board', False, 2
    capabilities = dict(ports='yes: lumped, from the reference copper to the trace', gap_ports='no',
                        diff_pairs='yes', parts='yes: one lumped element across the gap',
                        vias='yes: PEC cylinders', outline='approximated: dielectric over the outline\'s bounding box',
                        planes='yes', edge_walls='no', thickness='ignored: PEC sheets', conductor_loss='no',
                        dispersion='no: constant tan d', radiation='yes: first-order absorbing boundary',
                        cavity='yes', antenna='no')
    options = [
        Option('emerge_python', None, "the Python EMerge is installed in (default: $KICAD2EMS_EMERGE_PYTHON, else "
               "this one); EMerge 2.5+ needs Python <= 3.13", path=True, name='python', group=G,
               cli=dict(metavar='PATH')),
        Option('emerge_points', 11, "frequencies EMerge solves, spread over the band; a rational fit through them "
               "gives the run's points (default {default})", int, name='points', group=G, cli=dict(metavar='N')),
        Option('emerge_res', 0.2, "EMerge's mesh resolution: the largest element as a fraction of the wavelength "
               "(default {default})", float, name='res', group=G),
        Option('emerge_cell', None, "mm, the mesh size along copper edges and on ports (default: the narrowest port "
               "trace / 2)", float, name='cell', group=G, cli=dict(metavar='MM')),
        Option('emerge_margin', 3.0, "mm of air on every side of the board (default {default})", float,
               name='margin', group=G, cli=dict(metavar='MM')),
        Option('emerge_bc', 'abc', "outer boundary: abc (absorbing, default) or pec (a closed metal box, its floor "
               "the bottom plane when that is solid, as openEMS's --bc PEC)",
               choices=('abc', 'pec'), name='bc', group=G),
        Option('emerge_workers', 1, "frequencies solved at once (default {default}); each needs the memory of a "
               "whole solve (4.4 GB on the 34 mm microstrip thru)", int, name='workers', group=G,
               cli=dict(metavar='N')),
    ]

    def check(self, s=None):
        return emerge_status(emerge_python(s))

    def assess(self, run):
        m, out = run.m, []
        gaps = [p.name for p in m.ports if p.kind != 'z']
        if gaps:
            out.append(f"gap port(s) {', '.join(gaps)}: only ports on a trace over a plane")
        if run.s.antenna:
            out.append("antenna mode")
        return out

    def deviations(self, run, prep):
        m = run.m
        out = []
        if not m.outline.equals(m.outline.envelope):
            out.append(Deviation('outline', 'outline', "dielectric over the outline's bounding box"))
        out += [Deviation(f'wall:{i}', 'edge_walls', 'left out', dropped=True) for i in range(len(getattr(m, 'walls', [])))]
        return out

    def estimate(self, run):
        return None

    def solve(self, run, prep):
        s, m = run.s, run.m
        board = run.path('_board.json')
        if not os.path.exists(board):
            m.save(board)
        work = os.path.join(s.out, 'emerge')
        os.makedirs(work, exist_ok=True)
        cell = s.emerge_cell or max(0.05, min(p.w for p in m.ports) / 2)
        fs = np.linspace(run.fmin, run.fmax, max(2, s.emerge_points))
        job = dict(board=os.path.abspath(board), freqs=fs.tolist(), dense=list(map(float, run.freq)),
                   parts=[pad_span(m, pt) for pt in m.parts], bottom_plane=bottom_is_plane(m),
                   out=os.path.join(work, 'result.json'), res=s.emerge_res, cell=cell, margin=s.emerge_margin,
                   bc=s.emerge_bc, workers=max(1, s.emerge_workers))
        jp = os.path.join(work, 'job.json')
        json.dump(job, open(jp, 'w'), indent=1)
        if os.path.exists(job['out']):
            os.remove(job['out'])
        py = shutil.which(emerge_python(s)) or emerge_python(s)
        run.log(f"EMerge: {len(fs)} frequencies, cells {cell:.3g} mm along copper, resolution {s.emerge_res:g} "
                f"wavelengths, {s.emerge_bc.upper()} boundary {s.emerge_margin:g} mm out")
        t0 = time.time()
        logf = open(os.path.join(work, 'emerge.log'), 'w')
        env = dict(os.environ, PYTHONPATH='')                         # EMerge's own environment
        p = subprocess.Popen([py, WORKER, jp], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=env, bufsize=1)
        tail = []
        try:
            for line in p.stdout:
                logf.write(line)
                tail = (tail + [line.rstrip()])[-20:]
                if line.startswith('K2E '):
                    run.log(f"  EMerge: {line[4:].rstrip()}")
                elif 'Elapsed time taken' in line:
                    run.status('emerge', dict(t=time.time() - t0))
                if stop.requested():
                    p.kill()
                    raise stop.Stopped('stop requested')
        finally:
            p.wait()
            logf.close()
        if p.returncode in (-9, 137):
            raise RuntimeError(f"EMerge was killed, most likely out of memory: try a larger --emerge-cell or "
                               f"--emerge-res, or fewer --emerge-workers (log: {logf.name})")
        if p.returncode or not os.path.exists(job['out']):
            err = next((l for l in reversed(tail) if l.strip()), '')
            raise RuntimeError(f"EMerge exited with code {p.returncode}: {err[-200:]} (log: {logf.name})")
        r = json.load(open(job['out']))
        S = np.array(r['dense_re']) + 1j * np.array(r['dense_im']) if 'dense_re' in r else \
            _interp(np.array(r['f']), np.array(r['re']) + 1j * np.array(r['im']), run.freq)
        from ...sparams import mixed_mode
        Smm, names, zref = mixed_mode(S, m)
        secs = time.time() - t0
        run.log(f"EMerge: {secs:.0f} s, {r.get('tets', 0):,} tetrahedra")
        return Result(Smm, names, zref, secs, comment=f"EMerge {r.get('version', '')} FEM ({run.name})",
                      info=dict(version=r.get('version'), tets=r.get('tets'), solved_ghz=[f / 1e9 for f in r['f']],
                                cell_mm=cell, res=s.emerge_res, bc=s.emerge_bc,
                                kpi=f"{r.get('tets', 0):,} tetrahedra, {secs:.0f} s",
                                about=f"EMerge {r.get('version', '')}, a 3D finite-element solver: {r.get('tets', 0):,} "
                                      f"second-order tetrahedra, {len(r['f'])} frequencies solved and a rational fit "
                                      f"between them, lumped ports, PEC sheets, a {s.emerge_bc.upper()} boundary "
                                      f"{s.emerge_margin:g} mm from the board."))


def pad_span(m, part, step=0.005, reach=2.0):
    """The part as EMerge gets it: the element from pad to pad, where the copper covers its whole width, and
    that span cut out of the copper. kicad2ems's element starts where the copper of each side ends (parts.py:
    openEMS's metal wins over an element it overlaps); a track drawn to a pad's centre then touches it along
    only part of its width, and EMerge's resistive sheet carried the current through that part alone: the
    Wilkinson's 100 ohm resistor acted as about 200 ohm (isolation -16 dB against openEMS's -33 dB)."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    d = part.to_dict()
    a, o = part.axis, 1 - part.axis
    w0, w1 = sorted((part.s[o], part.e[o]))
    cu = unary_union(m.copper.get(part.layer, []))

    def covered(t):
        pts = [(t, w0), (t, w1)] if a == 0 else [(w0, t), (w1, t)]
        return cu.intersection(LineString(pts)).length >= 0.99 * (w1 - w0)
    lo, hi = sorted((part.s[a], part.e[a]))
    end = []
    for t0, sgn in ((lo, -1), (hi, 1)):
        t = t0
        while abs(t - t0) < reach and not covered(t + sgn * 1e-4):
            t += sgn * step
        end.append(t if abs(t - t0) < reach else t0)
    s_, e_ = list(part.s), list(part.e)
    s_[a], e_[a] = end
    s_[o], e_[o] = w0, w1
    d.update(s=[float(v) for v in s_], e=[float(v) for v in e_], cut=end != [lo, hi])
    return d


def _interp(f, S, freq):
    return np.stack([np.stack([np.interp(freq, f, S[:, i, j].real) + 1j * np.interp(freq, f, S[:, i, j].imag)
                               for j in range(S.shape[2])], -1) for i in range(S.shape[1])], 1)
