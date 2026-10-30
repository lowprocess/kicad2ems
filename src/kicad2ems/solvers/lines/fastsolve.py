"""
Fast S-parameters from the transmission-line sections (tlines.LineModel): the sections become a
circuit - lines, via barrels, lumped parts, ports at the port nodes, open ends left open - solved in
milliseconds by scikit-rf (in-process) or qucsator (Qucs-S's simulator, run as a separate program).

Each backend uses its own models where it has them: scikit-rf MLine (Hammerstad-Jensen with
Kirschning-Jansen dispersion) and CPW, qucsator MLIN and CLIN; every other section (stripline,
offset stripline, embedded microstrip, via barrels) is a line of the section's Z0 and effective
permittivity. By default the copper is zero-thickness, as openEMS models it (conducting sheets), so the
result is comparable with openEMS; sheet=False uses the stackup's copper thickness.

    S, names = solve(line_model, model.ports, freq, backend='skrf')      # S (nf, n, n), single-ended

A ballpark: no coupling between sections, no radiation, no plane resonances, quasi-static impedances.
"""
import math, os, re, shutil, subprocess, tempfile, warnings
import numpy as np
from ...constants import C0
from ...touchstone import write_touchstone

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
        for gid, g in enumerate(lm.groups):                 # coupled sections: one multiconductor block each
            ntw = _group_network(skrf, fr, g, sheet, f"group{gid}")
            n = len(g['members'])
            for k, (e, near, far) in enumerate(g['members']):
                nodes.setdefault(near, []).append((ntw, k))
                nodes.setdefault(far, []).append((ntw, n + k))
        for i, e in enumerate(lm.elements):
            name = f"e{i}"
            if e.kind == 'coupling' or e.group is not None:
                continue
            if e.kind == 'via' and e.b == 'gnd':
                ntw = shunt_z(1j * w * (e.L or 0.0) + 1e-6, name)
            elif e.kind == 'part':
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


def _group_network(skrf, fr, g, sheet, name):
    """A coupled section as a 2N-port skrf Network (ports: near ends, then far ends)."""
    S = group_s(g, fr.f)
    return skrf.Network(frequency=fr, s=S, z0=50.0, name=name)


def group_s(g, freq):
    """S-parameters (nf, 2N, 2N) of a coupled section of the line model (coupled.py)."""
    from ...lines.coupled import group_lc, mtl_s
    widths = [e.w for e, _, _ in g['members']]
    if g.get('C') is not None:                              # the field solver's (lines.reference.FIELD)
        C, Ca = g['C'], g['Ca']
    else:
        C, Ca = group_lc(widths, g['gaps'], g['h'], g['er'], g.get('xs', 'microstrip'))
    return mtl_s(C, Ca, g['er'], g['tand'] or 0.0, g['length'], freq)


# ---- qucsator ---------------------------------------------------------------------------------------
def find_qucsator(path=None):
    """The qucsator executable: the given path, else the one setup.sh recorded (sitecfg), else qucsator_rf
    (Qucs-S) or qucsator on PATH."""
    if not path:
        from ...sitecfg import get
        path = get('qucsator')
    if path:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise ValueError(f"qucsator not found at {path}")
    for name in ('qucsator_rf', 'qucsator'):
        exe = shutil.which(name)
        if exe:
            return exe
    raise ValueError("--fast qucsator: no qucsator_rf / qucsator on PATH (install Qucs-S, or give --qucsator PATH)")


def resolve_backend(backend='auto', qucsator=None):
    """(backend, what runs it) for a line-model solve: 'auto' is qucsator when it is installed (its coupled-line
    and discontinuity models), else scikit-rf. ValueError, saying how to fix it, when the backend cannot run."""
    if backend not in BACKENDS + ('auto',):
        raise ValueError(f"fast solver: choose one of auto, {', '.join(BACKENDS)}")
    if backend in ('auto', 'qucsator'):
        try:
            return 'qucsator', find_qucsator(qucsator)
        except ValueError as e:
            if backend == 'qucsator':
                raise ValueError(f"{e}; --fast skrf (or auto) solves the same line model with scikit-rf") from None
    try:
        import skrf
    except ImportError:
        raise ValueError("the line model needs scikit-rf (python -m pip install scikit-rf) or qucsator (Qucs-S) "
                         "on PATH" if backend == 'auto' else
                         "--fast skrf needs scikit-rf: python -m pip install scikit-rf") from None
    return 'skrf', f"scikit-rf {getattr(skrf, '__version__', '')}".strip()


def _junction_plan(lm):
    """For qucsator: the node each element end connects to (a discontinuity gets its own nodes per arm)
    and how much each end is shortened (lines are measured to the edges of a corner, tee or cross
    square, not to its centre as in the line model)."""
    endnode, short = {}, {}
    for k, j in enumerate(lm.junctions):
        if j['kind'] not in ('corner', 'step', 'tee', 'cross') or j['xs'] not in ('microstrip', 'cpwg'):
            continue
        if j['xs'] == 'cpwg' and j['kind'] != 'step':
            continue
        ws = j['widths']
        for a, (e, end) in enumerate(j['arms']):
            endnode[(id(e), end)] = f"{j['node']}#{k}.{a}"
            if e.group is not None:
                continue                                   # a coupled section keeps its common length
            if j['kind'] == 'corner':
                short[(id(e), end)] = ws[1 - a] / 2
            elif j['kind'] == 'tee':
                short[(id(e), end)] = ws[2] / 2 if a < 2 else 0.5 * (ws[0] + ws[1]) / 2
            elif j['kind'] == 'cross':
                short[(id(e), end)] = 0.5 * (ws[(a + 1) % 4] + ws[(a + 3) % 4]) / 2
    return endnode, short


RSTUB_MODEL, RSTUB_DIMENS = 'March', 'Chew_Kong'                # qucsator's MRSTUB: its model, its effective size
# The radial line starts half the main line's width beyond its edge, not at the edge: a fan whose point is on the
# line's centre does not carry a radial wave yet where it leaves the line. Fitted against mom25d on 60, 90 and
# 40 deg fans on 0.8 mm FR-4: notches within 0 / -0.5 / -2.4 % (at the edge: -17 to -20 %; the widening
# sections before this: +5 to +8 %)
RSTUB_RI_LINE = 0.5


def _radial_stubs(lm):
    """The microstrip radial stubs of the line model (strips.radial_sector, its sections tagged), each one MRSTUB
    in qucsator instead of its widening sections: [(element, end at the junction with the line, sector)] and the
    junctions inside the stubs (steps between the sections, the open end) to leave out. A stub that does not meet
    the line at one junction keeps its sections."""
    groups = {}
    for e in lm.elements:
        if getattr(e, 'radial', None) and e.xs == 'microstrip' and e.er and e.group is None:
            groups.setdefault(id(e.radial), (e.radial, []))[1].append(e)
    out, skip = [], set()
    for rad, els in groups.values():
        ids_ = {id(e) for e in els}
        inner, joins = [], []
        for k, j in enumerate(lm.junctions):
            mine = [(e, a) for e, a in j['arms'] if id(e) in ids_]
            if mine and len(mine) == len(j['arms']):
                inner.append(k)
            elif mine:
                joins.append((*mine[0], j))
        if len(joins) != 1:
            continue
        e, a, j = joins[0]
        line_w = max([w for (x, _), w in zip(j['arms'], j['widths']) if id(x) not in ids_] or [0.0])
        ri = rad['ri'] + RSTUB_RI_LINE * line_w
        if ri >= 0.8 * rad['ro']:
            continue
        rad = dict(rad, ri=ri, wf=2 * ri * math.tan(math.radians(rad['alpha'] / 2)), _elements=els)
        out.append((e, a, rad))
        skip.update(inner)
    return out, skip


def qucs_netlist(lm, ports, freq, sheet=True, sigma=None, workdir=None):
    """The line model as a qucsator netlist (S-parameter sweep over freq). Microstrip lines, corners,
    steps, tees, crosses, open ends, end gaps and vias to ground use qucsator's microstrip models
    (MLIN, MCORN, MSTEP, MTEE, MCROSS, MOPEN, MGAP, MVIA; a radial stub MRSTUB), grounded CPW its coplanar ones (CLIN, CSTEP,
    COPEN, CGAP), a coupled microstrip pair MCOUPLED; three or more coupled lines, a pair of unequal
    widths or coupled striplines are a multiconductor block (coupled.py) written to `workdir` as
    Touchstone and read with SPfile."""
    ids = {}

    def node(n):
        if n == 'gnd':
            return 'gnd'
        return ids.setdefault(n, f"n{len(ids)}")
    rho = 1 / sigma if sigma else 1e-15
    f0 = 0.5 * (freq[0] + freq[-1])
    L, subst = [], {}
    endnode, short = _junction_plan(lm)

    def end(e, k):
        n = e.a if k == 0 else e.b
        return node(endnode.get((id(e), k), n)) if n != 'gnd' else 'gnd'

    def sub(e, h=None, er=None, tand=None):
        h = e.h if h is None else h
        er = e.er if er is None else er
        tand = e.tand if tand is None else tand
        t = 1e-9 if sheet else max(e.t, 1e-6) * 1e-3
        key = (round(h, 6), round(er, 6), t, round(tand or 0.0, 6))
        if key not in subst:
            subst[key] = f"S{len(subst)}"
            L.append(f'SUBST:{subst[key]} er="{er:.6g}" h="{h * 1e-3:.6g}" t="{t:.6g}" tand="{tand or 0.0:.6g}" '
                     f'rho="{rho:.6g}" D="0"')
        return subst[key]
    ms = 'MSDispModel="Kirschning" MSModel="Hammerstad"'

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

    stubs, skip_j = _radial_stubs(lm)
    for e, k, rad in stubs:                                        # radial stubs: qucsator's own model
        L.append(f'MRSTUB:RS{len(L)} {end(e, k)} Subst="{sub(e)}" ri="{rad["ri"] * 1e-3:.6g}" '
                 f'ro="{rad["ro"] * 1e-3:.6g}" alpha="{rad["alpha"]:.6g}" Wf="{rad["wf"] * 1e-3:.6g}" '
                 f'EffDimens="{RSTUB_DIMENS}" Model="{RSTUB_MODEL}"')
    in_stub = {id(x) for e, _, rad in stubs for x in rad['_elements']}
    for i, e in enumerate(lm.elements):
        if e.kind == 'coupling' or e.group is not None or id(e) in in_stub:
            continue
        a, b = end(e, 0), end(e, 1)
        name = f"E{i}"
        length = max(e.length - short.get((id(e), 0), 0.0) - short.get((id(e), 1), 0.0), 1e-6)
        if e.kind == 'part':
            rlc(name, a, b, e.part)
        elif e.kind == 'via' and b == 'gnd':
            if e.er and e.h:
                L.append(f'MVIA:{name} {a} gnd D="{e.w * 1e-3:.6g}" Subst="{sub(e, h=e.h)}"')
            else:
                L.append(f'L:{name} {a} gnd L="{e.L:.9g} H"')
        elif e.xs == 'lumped' and e.L:
            L.append(f'L:{name} {a} {b} L="{e.L:.9g} H"')
        elif e.xs == 'microstrip' and e.er:
            L.append(f'MLIN:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" L="{length * 1e-3:.6g}" '
                     f'Model="Hammerstad" DispModel="Kirschning"')
        elif e.xs == 'cpwg' and e.er:
            L.append(f'CLIN:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" S="{e.s * 1e-3:.6g}" '
                     f'L="{length * 1e-3:.6g}" Backside="Metal" Approx="no"')
        elif e.xs == 'coax' and e.er and e.h:
            L.append(f'COAX:{name} {a} {b} D="{e.h * 1e-3:.6g}" d="{e.w * 1e-3:.6g}" L="{max(e.length, 1e-6) * 1e-3:.6g}" '
                     f'er="{e.er:.6g}" mur="1" tand="{e.tand or 0.0:.6g}" rho="{rho:.6g}"')
        elif e.z0:
            z0, ee = _line_params(e, sheet)
            lp, cp = z0 * math.sqrt(ee) / C0, math.sqrt(ee) / (z0 * C0)
            g = 2 * math.pi * f0 * cp * (e.tand or 0.0)          # dielectric loss matched at the band centre
            L.append(f'RLCG:{name} {a} {b} R="0" L="{lp:.9g}" C="{cp:.9g}" G="{g:.9g}" Length="{length * 1e-3:.9g}"')
        else:
            L.append(f'R:{name} {a} {b} R="1e-6 Ohm"')
    # discontinuities
    for k, j in enumerate(lm.junctions):
        e0 = j['arms'][0][0]
        if j['xs'] not in ('microstrip', 'cpwg') or not e0.er or k in skip_j:
            continue
        nm, ws = f"J{k}", j['widths']
        arms = [end(e, a) for e, a in j['arms']]
        if j['xs'] == 'microstrip':
            if j['kind'] == 'open':
                L.append(f'MOPEN:{nm} {arms[0]} Subst="{sub(e0)}" W="{ws[0] * 1e-3:.6g}" {ms} Model="Kirschning"')
            elif j['kind'] == 'gap':
                eb, kb = j['other']['arms'][0]
                L.append(f'MGAP:{nm} {arms[0]} {end(eb, kb)} Subst="{sub(e0)}" W1="{ws[0] * 1e-3:.6g}" '
                         f'W2="{eb.w * 1e-3:.6g}" S="{j["gap"] * 1e-3:.6g}" {ms}')
            elif j['kind'] == 'corner':
                L.append(f'MCORN:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" W="{0.5 * (ws[0] + ws[1]) * 1e-3:.6g}"')
            elif j['kind'] == 'step':
                L.append(f'MSTEP:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" W1="{ws[0] * 1e-3:.6g}" '
                         f'W2="{ws[1] * 1e-3:.6g}" {ms}')
            elif j['kind'] == 'tee':
                L.append(f'MTEE:{nm} {arms[0]} {arms[1]} {arms[2]} Subst="{sub(e0)}" ' +
                         " ".join(f'W{n + 1}="{w * 1e-3:.6g}"' for n, w in enumerate(ws)) + f' {ms}')
            elif j['kind'] == 'cross':
                L.append(f'MCROSS:{nm} {" ".join(arms)} Subst="{sub(e0)}" ' +
                         " ".join(f'W{n + 1}="{w * 1e-3:.6g}"' for n, w in enumerate(ws)) + f' {ms}')
        else:
            s_ = (e0.s or ws[0]) * 1e-3
            if j['kind'] == 'open':
                L.append(f'COPEN:{nm} {arms[0]} Subst="{sub(e0)}" W="{ws[0] * 1e-3:.6g}" S="{s_:.6g}" G="{s_:.6g}" '
                         f'Backside="Metal"')
            elif j['kind'] == 'gap':
                eb, kb = j['other']['arms'][0]
                L.append(f'CGAP:{nm} {arms[0]} {end(eb, kb)} Subst="{sub(e0)}" W="{ws[0] * 1e-3:.6g}" '
                         f'G="{j["gap"] * 1e-3:.6g}" S="{s_:.6g}"')
            elif j['kind'] == 'step':
                L.append(f'CSTEP:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" W1="{ws[0] * 1e-3:.6g}" '
                         f'W2="{ws[1] * 1e-3:.6g}" S="{max(s_, max(ws) * 1e-3 * 1.01):.6g}" Backside="Metal"')
    # coupled sections
    for gid, g in enumerate(lm.groups):
        mem = g['members']
        nodes_near = [end(e, 0 if near == e.a else 1) for e, near, far in mem]
        nodes_far = [end(e, 0 if far == e.a else 1) for e, near, far in mem]
        e0 = mem[0][0]
        ws = [e.w for e, _, _ in mem]
        if g.get('xs', 'microstrip') == 'microstrip' and len(mem) == 2 and abs(ws[0] - ws[1]) <= 0.05 * max(ws):
            L.append(f'MCOUPLED:G{gid} {nodes_near[0]} {nodes_far[0]} {nodes_far[1]} {nodes_near[1]} Subst="{sub(e0)}" '
                     f'W="{0.5 * (ws[0] + ws[1]) * 1e-3:.6g}" S="{g["gaps"][0] * 1e-3:.6g}" L="{g["length"] * 1e-3:.6g}" '
                     f'Model="Kirschning" DispModel="Kirschning"')
        else:
            import os
            if workdir is None:
                raise ValueError("a coupled section of more than two lines needs a working folder for its Touchstone")
            path = os.path.join(workdir, f"group{gid}.s{2 * len(mem)}p")
            write_touchstone(path, freq, group_s(g, freq), [50.0] * (2 * len(mem)), comment='coupled section')
            L.append(f'SPfile:G{gid} {" ".join(nodes_near + nodes_far)} gnd File="{{{path}}}" Data="rectangular" '
                     f'Interpolator="linear" duringDC="open"')
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
            if f'S[{i + 1},{j + 1}]' not in blocks:            # qucsator leaves out the terms between separate
                continue                                        # circuits (two legs not coupled): zero
            vals = blocks[f'S[{i + 1},{j + 1}]'].split()
            S[:, i, j] = [complex(v.replace('+j', '+').replace('-j', '-') + 'j') if 'j' in v else complex(float(v))
                          for v in vals]
    return f, S


def _qucsator(lm, ports, freq, sheet, sigma, exe, timeout=120.0):
    exe = find_qucsator(exe)
    with tempfile.TemporaryDirectory() as d:
        inp, out = os.path.join(d, 'line_model.net'), os.path.join(d, 'line_model.dat')
        text, order = qucs_netlist(lm, ports, freq, sheet, sigma, workdir=d)
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
    the backend's run time in seconds. backend 'auto': see resolve_backend."""
    import time
    if backend == 'auto':
        backend, _ = resolve_backend('auto', qucsator)
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
