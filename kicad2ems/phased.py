"""
A phased drive of an antenna with several ports (mode = antenna, drive = P1@0,P2@60): the far field of each port
driven alone, combined afterwards. The solve is linear, so this is exact at every frequency and keeps the coupling
between the elements: each port's column (its 1 V source, the other ports shorted) gives a far field E_k and the
ports' currents, Y[:, k]; incident waves a (the drive) need the port voltages

    a = (V + Zc Y V) / (2 sqrt(Zc))  ->  V = 2 (1 + Zc Y)^-1 sqrt(Zc) a,   b = (V - Zc Y V) / (2 sqrt(Zc))

and the far field is sum_k V_k E_k. Accepted power (|a|^2 - |b|^2) / 2, incident |a|^2 / 2, the active
reflection of each driven port b_k / a_k (what it sees with its neighbours driven too).

    a = parse_drive(['P1@0', 'P2=0.5@90'], names)      # incident wave per port (undriven: 0)
    a2 = steer(a, 30)                                   # a phase step of 30 deg from each driven port to the next
    g = parse_grid('4x4', names)                        # a 2D array: {port: (column, row)}, P1..P4 the top row
    a3 = steer2d(a, names, g, 30, -45)                  # +30 deg per column, -45 deg per row
    data = load(path)                                   # mom25d's <name>_mom25d_array.npz
    out = combine(data, a)                              # dict(sphere, rows, active, peak)

The .npz: theta, phi (deg; the upper hemisphere), freq (Hz), ports, zc (ohm), Y (nf, n, n), Eth, Eph (nf, n,
nphi, ntheta: r E per port column), drive (the run's a), and the sphere's theta (0..180, the lower half 0).
"""
import cmath, math, re
import numpy as np

ETA0 = 376.730313668


def parse_drive(spec, names):
    """Incident waves (complex, one per port of names) from PORT[=amplitude][@phase deg] items; a port not
    named is not driven (terminated). ValueError for an unknown port or a bad number."""
    a = np.zeros(len(names), complex)
    for item in spec or []:
        item = str(item).strip()
        if not item:
            continue
        name, _, phase = item.partition('@')
        name, _, amp = name.partition('=')
        name = name.strip()
        if name not in names:
            raise ValueError(f"drive: no port {name!r} (the ports: {', '.join(names)})")
        try:
            amp_v, ph_v = float(amp) if amp.strip() else 1.0, float(phase) if phase.strip() else 0.0
        except ValueError:
            raise ValueError(f"drive: {item!r}: expected PORT[=amplitude][@phase in degrees]")
        a[names.index(name)] = amp_v * cmath.exp(1j * math.radians(ph_v))
    return a


def format_drive(a, names):
    """The drive items of incident waves a (the driven ports), as parse_drive reads them."""
    out = []
    for n, v in zip(names, a):
        if abs(v) > 0:
            amp, ph = abs(v), math.degrees(cmath.phase(v))
            out.append(f"{n}{'' if abs(amp - 1) < 1e-9 else f'={amp:g}'}@{ph:.4g}")
    return out


def steer(a, step_deg):
    """The drive a with a phase step added from each driven port to the next (in port order): the first keeps
    its phase, the k-th driven port gets k x step more."""
    out = np.array(a, complex)
    k = 0
    for i, v in enumerate(out):
        if abs(v) > 0:
            out[i] = v * cmath.exp(1j * math.radians(k * step_deg))
            k += 1
    return out


def parse_grid(spec, names):
    """Each port's place in a 2D array, {port: (column, row)}, for steer2d. spec: empty (None: no grid),
    COLSxROWS (the ports in port order fill the rows, left to right, top row first; e.g. 4x4 for P1..P16), or
    PORT:COL,ROW items separated by spaces or semicolons (fractional places too: an offset, triangular lattice),
    e.g. 'P1:0,0 P2:1,0 P3:0,1 P4:1,1'. A port not placed gets no phase step. ValueError for an unknown port
    or a bad item."""
    spec = ' '.join(spec) if isinstance(spec, (list, tuple)) else str(spec or '')
    spec = spec.strip()
    if not spec:
        return None
    m = re.fullmatch(r'(\d+)\s*[xX×]\s*(\d+)', spec)
    if m:
        cols, rows = int(m.group(1)), int(m.group(2))
        if cols < 1 or rows < 1:
            raise ValueError(f"grid: {spec!r}: at least one column and one row")
        return {n: (k % cols, k // cols) for k, n in enumerate(names[:cols * rows])}
    out = {}
    for item in re.split(r'[\s;]+', spec.replace(', ', ',')):
        if not item:
            continue
        name, sep, place = item.rpartition(':')
        try:
            if not sep:
                raise ValueError
            col, row = (float(x) for x in place.split(','))
        except ValueError:
            raise ValueError(f"grid: {item!r}: expected COLSxROWS, or PORT:COLUMN,ROW per port")
        if name not in names:
            raise ValueError(f"grid: no port {name!r} (the ports: {', '.join(names)})")
        out[name] = (col, row)
    return out or None


def format_grid(grid, names):
    """The grid (parse_grid's) as a spec: COLSxROWS when it is the ports in order row by row, else the items."""
    if not grid:
        return ''
    cols = 1 + max(int(c) for c, _ in grid.values())
    rows = 1 + max(int(r) for _, r in grid.values())
    if parse_grid(f"{cols}x{rows}", names) == grid:
        return f"{cols}x{rows}"
    return ' '.join(f"{n}:{c:g},{r:g}" for n, (c, r) in grid.items())


def steer2d(a, names, grid, step_x, step_y):
    """The drive a (one per port of names) with phase steps across a 2D array: a driven port at (column, row) of
    grid (parse_grid's) gets column x step_x + row x step_y more phase (deg) on top of its own; ports off the
    grid are kept. No grid: steer(a, step_x), the 1D step from each driven port to the next."""
    if not grid:
        return steer(a, step_x)
    out = np.array(a, complex)
    for i, n in enumerate(names):
        if n in grid and abs(out[i]) > 0:
            c, r = grid[n]
            out[i] *= cmath.exp(1j * math.radians(c * step_x + r * step_y))
    return out


def settings_drive(drive, names, grid_spec=None, steps=None):
    """The incident waves of a run's settings: --drive (parse_drive), --steer's phase steps (X[,Y]) across
    --grid (steer2d), and the grid itself (None without one). ValueError for one that does not parse."""
    a = parse_drive(drive, names)
    g = parse_grid(grid_spec, names)
    steps = list(steps or [])
    if steps:
        sx, sy = (steps + [0.0])[:2]
        if sy and not g:
            raise ValueError("steer: a Y step needs --grid (the ports' rows)")
        a = steer2d(a, names, g, sx, sy)
    return a, g


def waves(Y, zc, a):
    """Port voltages V and reflected waves b for incident waves a (Y: port currents per 1 V column)."""
    zc = np.asarray(zc, float)
    rz = np.sqrt(zc)
    V = np.linalg.solve(np.eye(len(zc)) + zc[:, None] * Y, 2 * rz * a)
    b = (V - zc * (Y @ V)) / (2 * rz)
    return V, b


def radiated(Eth, Eph, theta, phi, eta=ETA0):
    """Radiation intensity U (W/sr, (nphi, ntheta)) and its integral over the grid (mom25d.farfield.radiated)."""
    U = (np.abs(Eth) ** 2 + np.abs(Eph) ** 2) / (2 * eta)
    th, ph = np.radians(theta), np.radians(phi)
    return U, float(np.trapezoid(np.trapezoid(U * np.sin(th)[None, :], th, axis=1), ph))


def combine(data, a, f_pattern=None):
    """The antenna of drive a from the per-port far fields: sphere (as load_sphere's: theta, phi, freq, dmax,
    prad, e_norm), rows (gain_table's), active ((nf, n) b / a of the driven ports, nan elsewhere) and the beam's
    peak at f_pattern (default the data's): dict(theta, phi, gain_dbi)."""
    from .solvers.openems.antenna import gain_table
    th, ph, freq = data['theta'], data['phi'], data['freq']
    th_s = data.get('theta_sphere', th)
    a = np.asarray(a, complex)
    driven = np.abs(a) > 0
    dmax, prad, pacc, pinc, e_norm, active = [], [], [], [], [], []
    for i in range(len(freq)):
        V, b = waves(data['Y'][i], data['zc'], a)
        Et = np.tensordot(V, data['Eth'][i], 1)
        Ep = np.tensordot(V, data['Eph'][i], 1)
        U, pr = radiated(Et, Ep, th, ph)
        dmax.append(4 * np.pi * U.max() / pr if pr > 0 else np.nan)
        prad.append(pr)
        pacc.append(0.5 * float(np.sum(np.abs(a) ** 2 - np.abs(b) ** 2)))
        pinc.append(0.5 * float(np.sum(np.abs(a) ** 2)))
        e = np.zeros((len(ph), len(th_s)))
        e[:, :len(th)] = np.sqrt(np.abs(Et) ** 2 + np.abs(Ep) ** 2)
        e_norm.append(e)
        act = np.full(len(a), np.nan + 0j)
        act[driven] = b[driven] / a[driven]
        active.append(act)
    rows = gain_table(freq, dmax, prad, pacc, pinc)
    f0 = float(data.get('f_pattern', freq[0]) if f_pattern is None else f_pattern)
    ip = int(np.argmin(np.abs(freq - f0)))
    e = e_norm[ip]
    j = np.unravel_index(int(np.argmax(e)), e.shape)
    g = dmax[ip] * rows[ip]['eff']
    peak = dict(theta=float(th_s[j[1]]), phi=float(ph[j[0]]), gain_dbi=float(10 * np.log10(g)) if g > 0 else None,
                f=float(freq[ip]))
    sphere = dict(theta=np.asarray(th_s), phi=np.asarray(ph), freq=np.asarray(freq), dmax=np.array(dmax),
                  prad=np.array(prad), e_norm=np.array(e_norm))
    return dict(sphere=sphere, rows=rows, active=np.array(active), peak=peak)


def save(path, *, theta, phi, theta_sphere, freq, ports, zc, Y, Eth, Eph, drive, f_pattern):
    np.savez_compressed(path, theta=theta, phi=phi, theta_sphere=theta_sphere, freq=freq, ports=np.array(ports),
                        zc=np.asarray(zc, float), Y=np.asarray(Y), Eth=np.asarray(Eth, np.complex64),
                        Eph=np.asarray(Eph, np.complex64), drive=np.asarray(drive, complex), f_pattern=f_pattern)


def load(path):
    with np.load(path) as z:
        d = {k: np.asarray(z[k]) for k in z.files}
    d['ports'] = [str(p) for p in d['ports']]
    d['Eth'], d['Eph'] = d['Eth'].astype(complex), d['Eph'].astype(complex)
    d['f_pattern'] = float(d['f_pattern'])
    return d
