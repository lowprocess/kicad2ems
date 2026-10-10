"""
Fast S-parameters from the transmission-line sections (tlines.LineModel): the sections become a
circuit - lines, via barrels, lumped parts, ports at the port nodes, open ends left open - solved in
milliseconds by scikit-rf (in-process) or qucsator (Qucs-S's simulator, run as a separate program).

Each backend uses its own models where it has them: scikit-rf MLine (Hammerstad-Jensen with
Kirschning-Jansen dispersion) and CPW, qucsator MLIN and CPWLINE; every other section (stripline,
offset stripline, embedded microstrip, via barrels) is a line of the section's Z0 and effective
permittivity. By default the copper is zero-thickness, as openEMS models it (conducting sheets), so the
result is comparable with openEMS; sheet=False uses the stackup's copper thickness.

    S, names = solve(line_model, model.ports, freq, backend='skrf')      # S (nf, n, n), single-ended

A ballpark: no coupling between sections, no radiation, no plane resonances, quasi-static impedances.
"""
import math, os, re, shutil, subprocess, tempfile, warnings
import numpy as np
from .constants import C0

BACKENDS = ('skrf', 'qucsator')


def part_impedance(part, w):
    """Impedance of a lumped part over angular frequencies w: R + jwL + 1/(jwC) of the values it has
    (series R-L-C; a single R, L or C alone)."""
    z = np.zeros_like(w, dtype=complex)
    if part.R is not None:
        z += part.R
    if part.L is not None:
        z += 1j * w * part.L
    if part.C is not None:
        z += 1 / (1j * w * part.C)
    return z


def _line_params(e, sheet):
    """(Z0, eeff) used for a section: zero-thickness values when sheet."""
    if sheet and e.z0_sheet is not None:
        return e.z0_sheet, e.eeff_sheet
    return e.z0, e.eeff


def _check_model(lm, ports):
    missing = [p.name for p in ports if p.name not in lm.ports]
    if len(missing) == len(ports):
        raise ValueError("no port has a line model (ports must sit on routed traces or pads)")
    return missing


# ---- scikit-rf --------------------------------------------------------------------------------------
def _skrf(lm, ports, freq, sheet, sigma):
    try:
        import skrf
        from skrf.circuit import Circuit
    except ImportError:
        raise ValueError("--fast skrf needs scikit-rf: python -m pip install scikit-rf") from None
    fr = skrf.Frequency.from_f(np.asarray(freq, float), unit='Hz')
    w = 2 * np.pi * fr.f
    rho = 1 / sigma if sigma else 1e-15
    nodes, net = {}, {}

    def series_z(z, name):
        s11 = z / (z + 100.0)
        s21 = 100.0 / (z + 100.0)
        return skrf.Network(frequency=fr, s=np.stack([np.stack([s11, s21], -1), np.stack([s21, s11], -1)], -2),
                            z0=50.0, name=name)

    def shunt_z(z, name):
        return skrf.Network(frequency=fr, s=((z - 50) / (z + 50))[:, None, None], z0=50.0, name=name)

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for i, e in enumerate(lm.elements):
            name = f"e{i}"
            if e.kind == 'part':
                z = part_impedance(e.part, w)
                if e.part.short:
                    z = np.full_like(w, 1e-6, dtype=complex)
                ntw = shunt_z(z, name) if e.b == 'gnd' else series_z(z, name)
            elif e.xs == 'lumped' and e.L:
                ntw = series_z(1j * w * e.L, name)
            elif e.xs in ('microstrip', 'cpwg') and e.er:
                t = 1e-9 if sheet else max(e.t, 1e-6) * 1e-3
                if e.xs == 'microstrip':
                    med = skrf.media.MLine(frequency=fr, w=e.w * 1e-3, h=e.h * 1e-3, t=t, ep_r=e.er, tand=e.tand,
                                           rho=rho, model='hammerstadjensen', disp='kirschningjansen',
                                           diel='frequencyinvariant', z0_port=50.0)
                else:
                    med = skrf.media.CPW(frequency=fr, w=e.w * 1e-3, s=e.s * 1e-3, h=e.h * 1e-3, ep_r=e.er,
                                         t=None if sheet else max(e.t, 1e-9) * 1e-3, has_metal_backside=True,
                                         rho=rho if sigma else None, tand=e.tand, diel='frequencyinvariant', z0_port=50.0)
                ntw = med.line(e.length * 1e-3, 'm', name=name)
            elif e.z0:
                z0, ee = _line_params(e, sheet)
                med = skrf.media.DefinedAEpTandZ0(frequency=fr, ep_r=ee, tanD=e.tand, z0=z0, z0_port=50.0)
                ntw = med.line(e.length * 1e-3, 'm', name=name)
            else:                                           # no reference plane: a plain connection
                ntw = series_z(np.full_like(w, 1e-6, dtype=complex), name)
            net[name] = ntw
            nodes.setdefault(e.a, []).append((ntw, 0))
            if ntw.nports == 2:
                nodes.setdefault(e.b, []).append((ntw, 1))
        for p in ports:
            if p.name in lm.ports:
                nodes.setdefault(lm.ports[p.name], []).append((Circuit.Port(fr, p.name, z0=p.z0), 0))
        for k, (node, conn) in enumerate(list(nodes.items())):
            if len(conn) == 1:
                conn.append((Circuit.Open(fr, f"open{k}"), 0))
        cir = Circuit(list(nodes.values()))
        res = cir.network
        return res.s, list(cir.port_names)


# ---- qucsator ---------------------------------------------------------------------------------------
def find_qucsator(path=None):
    """The qucsator executable: the given path, else qucsator_rf (Qucs-S) or qucsator on PATH."""
    if path:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise ValueError(f"qucsator not found at {path}")
    for name in ('qucsator_rf', 'qucsator'):
        exe = shutil.which(name)
        if exe:
            return exe
    raise ValueError("--fast qucsator: no qucsator_rf / qucsator on PATH (install Qucs-S, or give --qucsator PATH)")


def qucs_netlist(lm, ports, freq, sheet=True, sigma=None):
    """The line model as a qucsator netlist (S-parameter sweep over freq)."""
    ids = {}

    def node(n):
        if n == 'gnd':
            return 'gnd'
        return ids.setdefault(n, f"n{len(ids)}")
    rho = 1 / sigma if sigma else 1e-15
    f0 = 0.5 * (freq[0] + freq[-1])
    L, subst = [], {}

    def sub(e):
        t = 1e-9 if sheet else max(e.t, 1e-6) * 1e-3
        key = (round(e.h, 6), round(e.er, 6), t, round(e.tand, 6))
        if key not in subst:
            subst[key] = f"S{len(subst)}"
            L.append(f'SUBST:{subst[key]} er="{e.er:.6g}" h="{e.h * 1e-3:.6g}" t="{t:.6g}" tand="{e.tand:.6g}" '
                     f'rho="{rho:.6g}" D="0"')
        return subst[key]

    def rlc(name, a, b, part):
        """Series R-L-C chain between nodes a and b."""
        vals = [('R', part.R), ('L', part.L), ('C', part.C)]
        vals = [(k, v) for k, v in vals if v is not None] or [('R', 1e-6)]
        if part.short:
            vals = [('R', 1e-6)]
        chain = [a] + [f"{name}_{j}" for j in range(len(vals) - 1)] + [b]
        unit = dict(R='Ohm', L='H', C='F')
        for j, (k, v) in enumerate(vals):
            v = max(v, 1e-6) if k == 'R' else v
            L.append(f'{k}:{name}{k}{j} {chain[j]} {chain[j + 1]} {k}="{v:.9g} {unit[k]}"')

    for i, e in enumerate(lm.elements):
        a, b = node(e.a), node(e.b)
        name = f"E{i}"
        if e.kind == 'part':
            rlc(name, a, b, e.part)
        elif e.xs == 'lumped' and e.L:
            L.append(f'L:{name} {a} {b} L="{e.L:.9g} H"')
        elif e.xs == 'microstrip' and e.er:
            L.append(f'MLIN:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" L="{max(e.length, 1e-9) * 1e-3:.6g}" '
                     f'Model="Hammerstad" DispModel="Kirschning"')
        elif e.xs == 'cpwg' and e.er:
            L.append(f'CPWLINE:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" S="{e.s * 1e-3:.6g}" '
                     f'L="{max(e.length, 1e-9) * 1e-3:.6g}" Backside="Metal" Approx="no"')
        elif e.z0:
            z0, ee = _line_params(e, sheet)
            lp, cp = z0 * math.sqrt(ee) / C0, math.sqrt(ee) / (z0 * C0)
            g = 2 * math.pi * f0 * cp * e.tand                       # dielectric loss matched at the band centre
            L.append(f'RLCG:{name} {a} {b} R="0" L="{lp:.9g}" C="{cp:.9g}" G="{g:.9g}" Length="{e.length * 1e-3:.9g}"')
        else:
            L.append(f'R:{name} {a} {b} R="1e-6 Ohm"')
    num = 0
    order = []
    for p in ports:
        if p.name in lm.ports:
            num += 1
            order.append(p.name)
            L.append(f'Pac:P{num} {node(lm.ports[p.name])} gnd Num="{num}" Z="{p.z0:g} Ohm" P="0 dBm" f="1 GHz"')
    L.append(f'.SP:SP1 Type="lin" Start="{freq[0]:.9g}" Stop="{freq[-1]:.9g}" Points="{len(freq)}" Noise="no"')
    return "\n".join(L) + "\n", order


def read_qucs_dataset(text, n):
    """S (nf, n, n) from a qucsator dataset."""
    blocks = dict(re.findall(r'<(?:indep|dep) (\S+)[^>]*>\s*(.*?)\s*</(?:indep|dep)>', text, re.S))
    f = np.array([float(v) for v in blocks['frequency'].split()])
    S = np.zeros((len(f), n, n), complex)
    for i in range(n):
        for j in range(n):
            vals = blocks[f'S[{i + 1},{j + 1}]'].split()
            S[:, i, j] = [complex(v.replace('+j', '+').replace('-j', '-') + 'j') if 'j' in v else complex(float(v))
                          for v in vals]
    return f, S


def _qucsator(lm, ports, freq, sheet, sigma, exe, timeout=120.0):
    exe = find_qucsator(exe)
    text, order = qucs_netlist(lm, ports, freq, sheet, sigma)
    with tempfile.TemporaryDirectory() as d:
        inp, out = os.path.join(d, 'line_model.net'), os.path.join(d, 'line_model.dat')
        with open(inp, 'w') as fh:
            fh.write(text)
        try:
            r = subprocess.run([exe, '-i', inp, '-o', out], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"qucsator ({exe}) did not finish within {timeout:g} s") from None
        if r.returncode or not os.path.exists(out):
            raise RuntimeError(f"qucsator failed ({exe}):\n{(r.stdout + r.stderr)[-2000:]}")
        with open(out) as fh:
            _, S = read_qucs_dataset(fh.read(), len(order))
    return S, order


# ---- entry point ----------------------------------------------------------------------------------
def solve(lm, ports, freq, backend='skrf', sheet=True, sigma=None, qucsator=None):
    """S-parameters (nf, n, n) over `ports` (model.ports order; NaN for ports without a line model) and
    the backend's run time in seconds."""
    import time
    if backend not in BACKENDS:
        raise ValueError(f"fast solver: choose one of {', '.join(BACKENDS)}")
    _check_model(lm, ports)
    t0 = time.time()
    if backend == 'skrf':
        S, order = _skrf(lm, ports, freq, sheet, sigma)
    else:
        S, order = _qucsator(lm, ports, freq, sheet, sigma, qucsator)
    seconds = time.time() - t0
    names = [p.name for p in ports]
    full = np.full((len(freq), len(names), len(names)), np.nan + 0j)
    idx = [names.index(n) for n in order]
    comp = _components(lm)
    for a, i in enumerate(idx):
        for b, j in enumerate(idx):
            if comp.get(lm.ports[order[a]]) == comp.get(lm.ports[order[b]]):     # no path between them: unknown
                full[:, i, j] = S[:, a, b]
    return full, seconds


def _components(lm):
    """Connected part of the line model each node belongs to (through any element except ground)."""
    parent = {}

    def find(n):
        while parent.setdefault(n, n) != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for e in lm.elements:
        if e.b != 'gnd':
            parent[find(e.a)] = find(e.b)
        else:
            find(e.a)
    return {n: find(n) for n in list(parent)}
