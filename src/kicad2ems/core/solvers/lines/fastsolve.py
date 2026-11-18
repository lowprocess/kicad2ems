"""
Fast S-parameters from the transmission-line sections (tlines.LineModel): the
sections become a circuit - lines, via barrels, lumped parts, ports at the port
nodes, open ends left open - solved in milliseconds by scikit-rf (in-process) or
qucsator (Qucs-S's simulator, run as a separate program).

Each backend uses its own models where it has them: scikit-rf MLine
(Hammerstad-Jensen with Kirschning-Jansen dispersion) and CPW, qucsator MLIN and
CLIN; every other section (stripline, offset stripline, embedded microstrip, via
barrels, and any section the field solver solved: Element.field) is a line of
the section's Z0 and effective permittivity. By default the copper is
zero-thickness, as openEMS models it (conducting sheets), so the result is
comparable with openEMS; sheet=False uses the stackup's copper thickness.

    S, names = solve(line_model, model.ports, freq, backend='skrf')
        # S (nf, n, n), single-ended

A ballpark: no coupling between sections, no radiation, no plane resonances,
quasi-static impedances.
"""
import math, os, re, shutil, subprocess, sys, tempfile, warnings
import numpy as np
from ...constants import C0, MU0
from ...touchstone import log_spaced, write_touchstone

__all__ = ['resolve_backend', 'solve']

BACKENDS = ('skrf', 'qucsator')


def part_impedance(part, w):
    """Impedance of a lumped part over angular frequencies w: R + jwL + 1/(jwC)
    of the values it has (series R-L-C; a single R, L or C alone)."""
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


# ---- scikit-rf --------------------------------------------------------------
def conductor_r(w_mm, t_mm, f, sigma):
    """A line's series resistance per metre at f: the strip's surface resistance
    over its width (its DC sheet resistance where the skin is deeper than the
    copper) and its return's, about as much (2 Rs / w, as dfsynth's
    multiconductor model has it). A first cut: --sigma had reached only
    microstrip sections, and a stripline, a field-solved section or a coupled
    one was lossless whatever it said."""
    rs = np.sqrt(np.pi * np.asarray(f, float) * MU0 / sigma)
    rdc = 1.0 / (sigma * max(t_mm, 1e-4) * 1e-3)
    return (np.maximum(rs, rdc) + rs) / (w_mm * 1e-3)


def mode_tand(e):
    """The loss tangent of a section's mode, for a model of it as one effective
    medium (eeff, Z0): the field solver's (G / w C) where it solved it, else the
    substrate's scaled by its filling factor on a microstrip-like section (the
    substrate's own, taken whole, had been ~15 % high on a plain microstrip)."""
    if getattr(e, 'tand_eff', None):
        return float(e.tand_eff)
    tand, er, ee = e.tand or 0.0, e.er, e.eeff
    if (e.xs in ('microstrip', 'embedded microstrip', 'cpwg') and er and ee
            and er > 1 and 1 < ee < er):
        return tand * er / ee * (ee - 1) / (er - 1)
    return tand


def _skrf(lm, ports, freq, sheet, sigma):
    try:
        import skrf
        from skrf.circuit import Circuit
    except ImportError:
        raise ValueError(
            "--fast skrf needs scikit-rf: python -m pip install scikit-rf"
        ) from None
    from ...lines.formulas import gap_capacitance
    fr = skrf.Frequency.from_f(np.asarray(freq, float), unit='Hz')
    w = 2 * np.pi * fr.f
    rho = 1 / sigma if sigma else 1e-15
    nodes, net = {}, {}
    # a corner's arms to its square's edges, as qucsator's MCORN takes them,
    # its capacitance added at the node
    cut = {}
    for j in lm.junctions:
        if j['kind'] in ('corner', 'bend') and discontinuity_c(j, sheet):
            for (e, end), r in zip(j['arms'], bend_reach(j)):
                cut[id(e)] = cut.get(id(e), 0.0) + r
        # (R-E15: they had joined at their centre lines)
        elif (j['kind'] in ('tee', 'cross', 'junction')
              and j['xs'] == 'microstrip'):
            for (e, end), r in zip(j['arms'], junction_cuts(j)):
                if e.group is None and r:
                    cut[id(e)] = cut.get(id(e), 0.0) + r
    span = lambda e: max(e.length - cut.get(id(e), 0.0), 1e-3) * 1e-3     # (m)

    def series_z(z, name):
        s11 = z / (z + 100.0)
        s21 = 100.0 / (z + 100.0)
        return skrf.Network(
            frequency=fr,
            s=np.stack([np.stack([s11, s21], -1),
                        np.stack([s21, s11], -1)], -2), z0=50.0, name=name)

    def shunt_z(z, name):
        return skrf.Network(frequency=fr, s=((z - 50) /
                                             (z + 50))[:, None, None], z0=50.0,
                            name=name)

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        # coupled sections: one multiconductor block each
        for gid, g in enumerate(lm.groups):
            ntw = skrf.Network(frequency=fr, s=group_s(g, fr.f, sigma), z0=50.0,
                               name=f"group{gid}")
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
            # a part port: the port in the part's place
            elif e.kind == 'part' and getattr(e, 'port', None):
                zp = lm.port_z0.get(e.port, 50.0)
                if e.b == 'gnd':
                    nodes.setdefault(e.a, []).append(
                        (Circuit.Port(fr, e.port, z0=zp), 0))
                # across a series gap (R-E5): an ideal series tee (1, 2: its
                # nodes; 3: the port)
                else:
                    S = (np.array([[1, 2, 2], [2, 1, -2], [2, -2, 1]], float)
                         / 3.0)
                    tee = skrf.Network(
                        frequency=fr,
                        s=np.broadcast_to(S, (len(fr.f), 3, 3)).astype(complex),
                        z0=50.0, name=name)
                    nodes.setdefault(e.a, []).append((tee, 0))
                    nodes.setdefault(e.b, []).append((tee, 1))
                    nodes.setdefault(f"partport:{e.port}", []).extend([
                        (tee, 2), (Circuit.Port(fr, e.port, z0=zp), 0)
                    ])
                continue
            elif e.kind == 'part':
                z = (np.full_like(w, 1e-6, dtype=complex)
                     if e.part.short else part_impedance(e.part, w))
                ntw = shunt_z(z, name) if e.b == 'gnd' else series_z(z, name)
            elif e.xs == 'lumped' and e.L:
                ntw = series_z(1j * w * e.L, name)
            # field-solved: its own Z0 below
            elif e.xs in ('microstrip', 'cpwg') and e.er and not e.field:
                t = 1e-9 if sheet else max(e.t, 1e-6) * 1e-3
                if e.xs == 'microstrip':
                    med = skrf.media.MLine(
                        frequency=fr, w=e.w * 1e-3, h=e.h * 1e-3, t=t,
                        ep_r=e.er, tand=e.tand, rho=rho,
                        model='hammerstadjensen', disp='kirschningjansen',
                        diel='frequencyinvariant', z0_port=50.0)
                else:
                    med = skrf.media.CPW(
                        frequency=fr,
                        w=e.w * 1e-3,
                        s=e.s * 1e-3,
                        h=e.h * 1e-3,
                        ep_r=e.er,
                        t=(None if sheet and not sigma else
                           max(e.t or 0.035, 1e-9) * 1e-3),
                        # (a thickness: its loss needs one)
                        has_metal_backside=True,
                        rho=rho if sigma else None,
                        tand=e.tand,
                        diel='frequencyinvariant',
                        z0_port=50.0)
                ntw = med.line(span(e), 'm', name=name)
            elif e.z0:
                z0, ee = _line_params(e, sheet)
                if sigma:  # the copper's loss too: gamma of R, L, G, C
                    beta = w * np.sqrt(ee) / C0
                    gamma = (1j * beta * np.sqrt(1 - 1j * mode_tand(e))
                             + conductor_r(e.w, e.t or 0.035, fr.f, sigma) /
                             (2 * z0))
                    med = skrf.media.DefinedGammaZ0(frequency=fr, z0=z0,
                                                    gamma=gamma, z0_port=50.0)
                else:
                    med = skrf.media.DefinedAEpTandZ0(frequency=fr, ep_r=ee,
                                                      tanD=mode_tand(e), z0=z0,
                                                      z0_port=50.0)
                ntw = med.line(span(e), 'm', name=name)
            else:  # no reference plane: a plain connection
                ntw = series_z(np.full_like(w, 1e-6, dtype=complex), name)
            net[name] = ntw
            nodes.setdefault(e.a, []).append((ntw, 0))
            if ntw.nports == 2:
                nodes.setdefault(e.b, []).append((ntw, 1))
        # end gaps: a pi of capacitances (Garg and Bahl)
        for k, j in enumerate(lm.junctions):
            if j['kind'] != 'gap':
                continue
            (ea, _), (eb, _) = j['arms'][0], j['other']['arms'][0]
            cp, cg = (gap_capacitance(0.5 * (ea.w + eb.w), j['gap'], ea.h,
                                      ea.er) if ea.er and ea.h else (0.0, 0.0))
            yp, yg = 1j * w * cp, 1j * w * cg
            Y = np.zeros((len(w), 2, 2), complex)
            Y[:, 0, 0] = Y[:, 1, 1] = yp + yg
            Y[:, 0, 1] = Y[:, 1, 0] = -yg
            ntw = skrf.Network(frequency=fr, s=skrf.network.y2s(Y, z0=50.0),
                               z0=50.0, name=f"gap{k}")
            nodes.setdefault(j['node'], []).append((ntw, 0))
            nodes.setdefault(j['other']['node'], []).append((ntw, 1))
        # open ends, steps, bends: their excess capacitance at the node
        # (R-E15: this backend had none of them)
        for k, j in enumerate(lm.junctions):
            c = discontinuity_c(j, sheet)
            if c:
                nodes.setdefault(j['node'], []).append(
                    (shunt_z(1 / (1j * w * c), f"disc{k}"), 0))
        for p in ports:
            if p.name in lm.ports and not isinstance(lm.ports[p.name], tuple):
                nodes.setdefault(lm.ports[p.name], []).append(
                    (Circuit.Port(fr, p.name, z0=p.z0), 0))
        for k, (node, conn) in enumerate(list(nodes.items())):
            if len(conn) == 1:
                conn.append((Circuit.Open(fr, f"open{k}"), 0))
        cir = Circuit(list(nodes.values()))
        return cir.network.s, list(cir.port_names)


def junction_cuts(j):
    """How far each arm of a junction of three or more arms (a tee, a cross,
    five or more) starts from its node (mm), for the backends without a model of
    it: the widest straight run through it (two arms facing each other) runs on
    uncut, its centre line the junction's; every other arm starts at that run's
    edge, half its width out (its centre line had reached the node across the
    run's copper). With no straight run, each arm starts at the widest arm
    across it, half that width out. [cut per arm, in j['arms']'s order]."""
    ws, ds = j['widths'], np.asarray(j.get('dirs') or [], float)
    n = len(ws)
    if len(ds) != n:
        return [0.0] * n
    pairs = [(a, b)
             for a in range(n)
             for b in range(a + 1, n)
             if np.dot(ds[a], ds[b]) < -0.9]
    if pairs:
        a, b = max(pairs, key=lambda ab: ws[ab[0]] + ws[ab[1]])
        run = 0.5 * (ws[a] + ws[b])
        return [0.0 if k in (a, b) else run / 2 for k in range(n)]
    return [
        max((ws[m]
             for m in range(n)
             if m != k and abs(np.dot(ds[k], ds[m])) < 0.9), default=0.0) / 2
        for k in range(n)
    ]


def bend_reach(j):
    """How far a corner's square reaches along each of its two arms (mm): half
    the other arm's width (a 90 deg corner, qucsator's MCORN); a bend of another
    angle none (its capacitance alone, at the vertex)."""
    if j['kind'] == 'bend':
        # another angle than 90: the arms keep their centre lines to the vertex
        # (the current turns along it). Cut to the corner square's edges, as at
        # a 90 deg corner, rf_coupler's eight 45 deg bends had moved it from
        # 0.029 to 0.060 from openEMS (0.078 with the cut alone); with the
        # capacitance alone 0.028 (R-E15, 2026-11-15)
        return [0.0, 0.0]
    t = math.tan(math.radians(min(max(j.get('angle', 90.0), 0.0), 135.0)) / 2)
    return [j['widths'][1] / 2 * t, j['widths'][0] / 2 * t]


def discontinuity_c(j, sheet=True):
    """The excess capacitance (F) a line-model junction j adds at its node, for
    the backends without models of their own: a microstrip's open end
    (Kirschning, Jansen and Koster's length extension), a width step (Edwards'),
    a corner or another bend (Gupta, Garg and Bahl's, scaled by the corner's
    area): its extension's length of line capacitance, C' = sqrt(eeff) / (c Z0).
    None for any other junction, or one off a microstrip."""
    from ...lines.formulas import (open_end_extension, step_extension,
                                   bend_capacitance)
    if (j['xs'] != 'microstrip'
            or j['kind'] not in ('open', 'step', 'corner', 'bend')):
        return None
    arms = [e for e, _ in j['arms']]
    if any(e.group is not None or not e.er or not e.h or not e.w for e in arms):
        return None
    if j['kind'] in ('open', 'step'):
        e = arms[0] if j['kind'] == 'open' else max(arms, key=lambda x: x.w)
        z0, ee = _line_params(e, sheet)
        if not (z0 and ee):
            return None
        ext = (open_end_extension(e.w, e.h, e.er, ee) if j['kind'] == 'open'
               else step_extension(arms[0].w, arms[1].w, e.h, e.er, ee))
        return ext * 1e-3 * math.sqrt(ee) / (C0 * z0)
    return bend_capacitance(0.5 * (arms[0].w + arms[1].w), arms[0].h,
                            arms[0].er, j.get('angle', 90.0))


def group_s(g, freq, sigma=None):
    """S-parameters (nf, 2N, 2N) of a coupled section of the line model
    (coupled.py); sigma: its copper's loss."""
    from ...lines.coupled import group_lc, mtl_s
    members = [e for e, _, _ in g['members']]
    if g.get('C') is not None:  # the field solver's (lines.reference.FIELD)
        C, Ca = g['C'], g['Ca']
    else:
        C, Ca = group_lc([e.w for e in members], g['gaps'], g['h'], g['er'],
                         g.get('xs', 'microstrip'))
    R = ((lambda f: np.array(
        [conductor_r(e.w, e.t or 0.035, f, sigma)
         for e in members])) if sigma else None)
    return mtl_s(C, Ca, g['er'], g['tand'] or 0.0, g['length'], freq, R=R)


# ---- qucsator ---------------------------------------------------------------
def find_qucsator(path=None):
    """The qucsator executable: the given path, else the one the installer
    recorded (sitecfg), else qucsator_rf (Qucs-S) or qucsator on PATH, else
    (Windows) the one in Qucs-S's install folder."""
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
    # Qucs-S's installer does not put its bin folder on the PATH: Qucs-S's
    # (or Qucs's) under Program Files, newest
    if sys.platform == 'win32':
        import glob
        for base in dict.fromkeys(
                filter(None, (os.environ.get('ProgramFiles'),
                              os.environ.get('ProgramW6432'),
                              os.environ.get('ProgramFiles(x86)')))):
            for name in ('qucsator_rf.exe', 'qucsator.exe'):
                found = sorted(
                    glob.glob(os.path.join(base, 'Qucs*', 'bin', name)),
                    reverse=True)
                if found:
                    return found[0]
    raise ValueError(
        "--fast qucsator: no qucsator_rf / qucsator on PATH (install Qucs-S, "
        "or give --qucsator PATH)"
    )


def resolve_backend(backend='auto', qucsator=None):
    """(backend, what runs it) for a line-model solve: 'auto' is qucsator when
    it is installed (its coupled-line and discontinuity models), else scikit-rf.
    ValueError, saying how to fix it, when the backend cannot run."""
    if backend not in BACKENDS + ('auto',):
        raise ValueError(
            f"fast solver: choose one of auto, {', '.join(BACKENDS)}")
    if backend in ('auto', 'qucsator'):
        try:
            return 'qucsator', find_qucsator(qucsator)
        except ValueError as e:
            if backend == 'qucsator':
                raise ValueError(
                    f"{e}; --fast skrf (or auto) solves the same line model "
                    "with scikit-rf"
                ) from None
    try:
        import skrf
    except ImportError:
        raise ValueError(
            "the line model needs scikit-rf (python -m pip install scikit-rf) "
            "or qucsator (Qucs-S) on PATH" if backend == 'auto' else
            "--fast skrf needs scikit-rf: python -m pip install scikit-rf"
        ) from None
    return 'skrf', f"scikit-rf {getattr(skrf, '__version__', '')}".strip()


def _junction_plan(lm):
    """For qucsator: the node each element end connects to (a discontinuity gets
    its own nodes per arm) and how much each end is shortened (lines are
    measured to the edges of a corner, tee or cross square, not to its centre as
    in the line model)."""
    endnode, short = {}, {}
    for k, j in enumerate(lm.junctions):
        if (j['kind'] not in ('corner', 'step', 'tee', 'cross')
                or j['xs'] not in ('microstrip', 'cpwg')):
            continue
        if j['xs'] == 'cpwg' and j['kind'] != 'step':
            continue
        ws = j['widths']
        for a, (e, end) in enumerate(j['arms']):
            endnode[(id(e), end)] = f"{j['node']}#{k}.{a}"
            if e.group is not None:
                continue  # a coupled section keeps its common length
            if j['kind'] == 'corner':
                short[(id(e), end)] = ws[1 - a] / 2
            elif j['kind'] == 'tee':
                short[(id(e), end)] = (ws[2] / 2 if a < 2 else 0.5 *
                                       (ws[0] + ws[1]) / 2)
            elif j['kind'] == 'cross':
                short[(id(e), end)] = (0.5 *
                                       (ws[(a + 1) % 4] + ws[(a + 3) % 4]) / 2)
    # a bend of another angle: its arms to its square's edges
    for j in lm.junctions:
        # too, its capacitance at the node (one node)
        if (j['kind'] == 'bend' and j['xs'] == 'microstrip'
                and discontinuity_c(j)):
            for (e, end), r in zip(j['arms'], bend_reach(j)):
                short[(id(e), end)] = r
        # five arms or more: no element; the arms start at the widest straight
        # run's edge (R-E15)
        elif j['kind'] == 'junction' and j['xs'] == 'microstrip':
            for (e, end), r in zip(j['arms'], junction_cuts(j)):
                if e.group is None and r:
                    short[(id(e), end)] = r
    return endnode, short


# qucsator's MRSTUB: its model, its effective size
RSTUB_MODEL, RSTUB_DIMENS = 'March', 'Chew_Kong'
# The radial line starts half the main line's width beyond its edge, not at the
# edge: a fan whose point is on the line's centre does not carry a radial wave
# yet where it leaves the line. Fitted against mom25d on 60, 90 and 40 deg fans
# on 0.8 mm FR-4: notches within 0 / -0.5 / -2.4 % (at the edge: -17 to -20 %;
# the widening sections before this: +5 to +8 %)
RSTUB_RI_LINE = 0.5


def _radial_stubs(lm):
    """The microstrip radial stubs of the line model (strips.radial_sector, its
    sections tagged), each one MRSTUB in qucsator instead of its widening
    sections: [(element, end at the junction with the line, sector)] and the
    junctions inside the stubs (steps between the sections, the open end) to
    leave out. A stub that does not meet the line at one junction keeps its
    sections."""
    groups = {}
    for e in lm.elements:
        if (getattr(e, 'radial', None) and e.xs == 'microstrip' and e.er
                and e.group is None):
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
        line_w = max([
            w for (x, _), w in zip(j['arms'], j['widths']) if id(x) not in ids_
        ] or [0.0])
        ri = rad['ri'] + RSTUB_RI_LINE * line_w
        if ri >= 0.8 * rad['ro']:
            continue
        rad = dict(rad, ri=ri,
                   wf=2 * ri * math.tan(math.radians(rad['alpha'] / 2)),
                   _elements=els)
        out.append((e, a, rad))
        skip.update(inner)
    return out, skip


def qucs_netlist(lm, ports, freq, sheet=True, sigma=None, workdir=None):
    """The line model as a qucsator netlist (S-parameter sweep over freq).
    Microstrip lines, corners, steps, tees, crosses, open ends, end gaps and
    vias to ground use qucsator's microstrip models (MLIN, MCORN, MSTEP, MTEE,
    MCROSS, MOPEN, MGAP, MVIA; a radial stub MRSTUB), grounded CPW its coplanar
    ones (CLIN, CSTEP, COPEN, CGAP), a coupled microstrip pair MCOUPLED; three
    or more coupled lines, a pair of unequal widths or coupled striplines are a
    multiconductor block (coupled.py) written to `workdir` as Touchstone and
    read with SPfile."""
    ids = {}

    def node(n):
        return 'gnd' if n == 'gnd' else ids.setdefault(n, f"n{len(ids)}")
    rho = 1 / sigma if sigma else 1e-15
    f0 = 0.5 * (freq[0] + freq[-1])
    L, subst, pac = [], {}, {}               # (pac: a part port's two nodes)
    endnode, short = _junction_plan(lm)

    def end(e, k):
        n = e.a if k == 0 else e.b
        return node(endnode.get((id(e), k), n)) if n != 'gnd' else 'gnd'

    def sub(e):
        t = 1e-9 if sheet else max(e.t, 1e-6) * 1e-3
        key = (round(e.h, 6), round(e.er, 6), t, round(e.tand or 0.0, 6))
        if key not in subst:
            subst[key] = f"S{len(subst)}"
            L.append(
                f'SUBST:{subst[key]} er="{e.er:.6g}" h="{e.h * 1e-3:.6g}" '
                f't="{t:.6g}" tand="{e.tand or 0.0:.6g}" rho="{rho:.6g}" D="0"')
        return subst[key]
    ms = 'MSDispModel="Kirschning" MSModel="Hammerstad"'

    def rlc(name, a, b, part):
        """Series R-L-C chain between nodes a and b."""
        vals = [(k, v)
                for k, v in (('R', part.R), ('L', part.L), ('C', part.C))
                if v is not None and not part.short]
        vals = vals or [('R', 1e-6)]
        chain = [a] + [f"{name}_{j}" for j in range(len(vals) - 1)] + [b]
        unit = dict(R='Ohm', L='H', C='F')
        for j, (k, v) in enumerate(vals):
            v = max(v, 1e-6) if k == 'R' else v
            L.append(
                f'{k}:{name}{k}{j} {chain[j]} {chain[j + 1]} {k}="{v:.9g} '
                f'{unit[k]}"'
            )

    stubs, skip_j = _radial_stubs(lm)
    for e, k, rad in stubs:  # radial stubs: qucsator's own model
        L.append(
            f'MRSTUB:RS{len(L)} {end(e, k)} Subst="{sub(e)}" '
            f'ri="{rad["ri"] * 1e-3:.6g}" ro="{rad["ro"] * 1e-3:.6g}" '
            f'alpha="{rad["alpha"]:.6g}" Wf="{rad["wf"] * 1e-3:.6g}" '
            f'EffDimens="{RSTUB_DIMENS}" Model="{RSTUB_MODEL}"')
    in_stub = {id(x) for e, _, rad in stubs for x in rad['_elements']}
    for i, e in enumerate(lm.elements):
        if e.kind == 'coupling' or e.group is not None or id(e) in in_stub:
            continue
        a, b = end(e, 0), end(e, 1)
        name = f"E{i}"
        length = max(
            e.length - short.get((id(e), 0), 0.0) - short.get((id(e), 1), 0.0),
            1e-6)
        # a part port: a Pac across it (below)
        if e.kind == 'part' and getattr(e, 'port', None):
            pac[e.port] = (a, b)
        elif e.kind == 'part':
            rlc(name, a, b, e.part)
        elif e.kind == 'via' and b == 'gnd':
            if e.er and e.h:
                L.append(
                    f'MVIA:{name} {a} gnd D="{e.w * 1e-3:.6g}" Subst="{sub(e)}"'
                )
            else:
                L.append(f'L:{name} {a} gnd L="{e.L:.9g} H"')
        elif e.xs == 'lumped' and e.L:
            L.append(f'L:{name} {a} {b} L="{e.L:.9g} H"')
        elif e.xs == 'microstrip' and e.er and not e.field:
            L.append(
                f'MLIN:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" '
                f'L="{length * 1e-3:.6g}" Model="Hammerstad" '
                'DispModel="Kirschning"')
        elif e.xs == 'cpwg' and e.er and not e.field:
            L.append(
                f'CLIN:{name} {a} {b} Subst="{sub(e)}" W="{e.w * 1e-3:.6g}" '
                f'S="{e.s * 1e-3:.6g}" L="{length * 1e-3:.6g}" '
                'Backside="Metal" Approx="no"')
        elif e.xs == 'coax' and e.er and e.h:
            L.append(
                f'COAX:{name} {a} {b} D="{e.h * 1e-3:.6g}" '
                f'd="{e.w * 1e-3:.6g}" L="{max(e.length, 1e-6) * 1e-3:.6g}" '
                f'er="{e.er:.6g}" mur="1" tand="{e.tand or 0.0:.6g}" '
                f'rho="{rho:.6g}"'
            )
        elif e.z0:
            z0, ee = _line_params(e, sheet)
            lp, cp = z0 * math.sqrt(ee) / C0, math.sqrt(ee) / (z0 * C0)
            # dielectric loss matched at the band centre
            g = 2 * math.pi * f0 * cp * mode_tand(e)
            # (at the band centre, as G)
            r = (float(conductor_r(e.w, e.t or 0.035, f0, sigma))
                 if sigma else 0.0)
            L.append(
                f'RLCG:{name} {a} {b} R="{r:.9g}" L="{lp:.9g}" C="{cp:.9g}" '
                f'G="{g:.9g}" Length="{length * 1e-3:.9g}"'
            )
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
                L.append(
                    f'MOPEN:{nm} {arms[0]} Subst="{sub(e0)}" '
                    f'W="{ws[0] * 1e-3:.6g}" {ms} Model="Kirschning"'
                )
            elif j['kind'] == 'gap':
                eb, kb = j['other']['arms'][0]
                L.append(
                    f'MGAP:{nm} {arms[0]} {end(eb, kb)} Subst="{sub(e0)}" '
                    f'W1="{ws[0] * 1e-3:.6g}" W2="{eb.w * 1e-3:.6g}" '
                    f'S="{j["gap"] * 1e-3:.6g}" {ms}')
            elif j['kind'] == 'corner':
                L.append(
                    f'MCORN:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" '
                    f'W="{0.5 * (ws[0] + ws[1]) * 1e-3:.6g}"'
                )
            elif j['kind'] == 'step':
                L.append(
                    f'MSTEP:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" '
                    f'W1="{ws[0] * 1e-3:.6g}" W2="{ws[1] * 1e-3:.6g}" {ms}')
            elif j['kind'] in ('tee', 'cross'):
                L.append(
                    f'{"MTEE" if j["kind"] == "tee" else "MCROSS"}:{nm} '
                    f'{" ".join(arms)} Subst="{sub(e0)}" '
                    + " ".join(f'W{n + 1}="{w * 1e-3:.6g}"'
                               for n, w in enumerate(ws)) + f' {ms}')
            # another angle than MCORN's: its capacitance (R-E15)
            elif j['kind'] == 'bend':
                c = discontinuity_c(j, sheet)
                if c:
                    L.append(f'C:{nm} {arms[0]} gnd C="{c:.6g}"')
        else:
            s_ = (e0.s or ws[0]) * 1e-3
            if j['kind'] == 'open':
                L.append(
                    f'COPEN:{nm} {arms[0]} Subst="{sub(e0)}" '
                    f'W="{ws[0] * 1e-3:.6g}" S="{s_:.6g}" G="{s_:.6g}" '
                    'Backside="Metal"')
            elif j['kind'] == 'gap':
                eb, kb = j['other']['arms'][0]
                L.append(
                    f'CGAP:{nm} {arms[0]} {end(eb, kb)} Subst="{sub(e0)}" '
                    f'W="{ws[0] * 1e-3:.6g}" G="{j["gap"] * 1e-3:.6g}" '
                    f'S="{s_:.6g}"')
            elif j['kind'] == 'step':
                L.append(
                    f'CSTEP:{nm} {arms[0]} {arms[1]} Subst="{sub(e0)}" '
                    f'W1="{ws[0] * 1e-3:.6g}" W2="{ws[1] * 1e-3:.6g}" '
                    f'S="{max(s_, max(ws) * 1e-3 * 1.01):.6g}" Backside="Metal"'
                )
    # coupled sections
    for gid, g in enumerate(lm.groups):
        mem = g['members']
        nodes_near = [end(e, 0 if near == e.a else 1) for e, near, far in mem]
        nodes_far = [end(e, 0 if far == e.a else 1) for e, near, far in mem]
        e0 = mem[0][0]
        ws = [e.w for e, _, _ in mem]
        # (its own model knows nothing of the board's cut)
        if (g.get('xs', 'microstrip') == 'microstrip' and len(mem) == 2
                and abs(ws[0] - ws[1]) <= 0.05 * max(ws)
                and g.get('source') != 'cut'):
            L.append(
                f'MCOUPLED:G{gid} {nodes_near[0]} {nodes_far[0]} '
                f'{nodes_far[1]} {nodes_near[1]} Subst="{sub(e0)}" '
                f'W="{0.5 * (ws[0] + ws[1]) * 1e-3:.6g}" '
                f'S="{g["gaps"][0] * 1e-3:.6g}" L="{g["length"] * 1e-3:.6g}" '
                'Model="Kirschning" DispModel="Kirschning"')
        else:
            if workdir is None:
                raise ValueError(
                    "a coupled section of more than two lines needs a working "
                    "folder for its Touchstone"
                )
            path = os.path.join(workdir, f"group{gid}.s{2 * len(mem)}p")
            write_touchstone(path, freq, group_s(g, freq, sigma), [50.0] *
                             (2 * len(mem)), comment='coupled section')
            # (outside the f-string: Python 3.10 and 3.11)
            unix = path.replace(os.sep, '/')
            L.append(
                f'SPfile:G{gid} {" ".join(nodes_near + nodes_far)} gnd '
                f'File="{{{unix}}}" Data="rectangular" Interpolator="linear" '
                'duringDC="open"')
    order = [p.name for p in ports if p.name in lm.ports]
    for num, p in enumerate((p for p in ports if p.name in lm.ports), 1):
        a, b = pac.get(p.name) or (node(lm.ports[p.name]), 'gnd')
        L.append(
            f'Pac:P{num} {a} {b} Num="{num}" Z="{p.z0:g} Ohm" P="0 dBm" f="1 '
            'GHz"'
        )
    kind = 'log' if log_spaced(freq) else 'lin'
    L.append(
        f'.SP:SP1 Type="{kind}" Start="{freq[0]:.9g}" Stop="{freq[-1]:.9g}" '
        f'Points="{len(freq)}" Noise="no"'
    )
    return "\n".join(L) + "\n", order


def read_qucs_dataset(text, n):
    """S (nf, n, n) from a qucsator dataset."""
    blocks = dict(
        re.findall(r'<(?:indep|dep) (\S+)[^>]*>\s*(.*?)\s*</(?:indep|dep)>',
                   text, re.S))
    f = np.array([float(v) for v in blocks['frequency'].split()])
    S = np.zeros((len(f), n, n), complex)
    for i in range(n):
        for j in range(n):
            # qucsator leaves out the terms between separate circuits (two
            # legs not coupled): zero
            if f'S[{i + 1},{j + 1}]' not in blocks:
                continue
            vals = blocks[f'S[{i + 1},{j + 1}]'].split()
            S[:, i, j] = [
                complex(v.replace('+j', '+').replace('-j', '-')
                        + 'j') if 'j' in v else complex(float(v)) for v in vals
            ]
    return f, S


def _qucsator(lm, ports, freq, sheet, sigma, exe, timeout=120.0):
    exe = find_qucsator(exe)
    with tempfile.TemporaryDirectory() as d:
        inp, out = (os.path.join(d, 'line_model.net'),
                    os.path.join(d, 'line_model.dat'))
        text, order = qucs_netlist(lm, ports, freq, sheet, sigma, workdir=d)
        with open(inp, 'w', encoding='utf-8') as fh:
            fh.write(text)
        try:
            r = subprocess.run([exe, '-i', inp, '-o', out], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"qucsator ({exe}) did not finish within {timeout:g} s"
            ) from None
        if r.returncode or not os.path.exists(out):
            raise RuntimeError(
                f"qucsator failed ({exe}):\n{(r.stdout + r.stderr)[-2000:]}")
        with open(out, encoding='utf-8') as fh:
            _, S = read_qucs_dataset(fh.read(), len(order))
    return S, order


# ---- entry point ------------------------------------------------------------
def solve(lm, ports, freq, backend='skrf', sheet=True, sigma=None,
          qucsator=None, wideband=None, info=None):
    """S-parameters (nf, n, n) over `ports` (model.ports order; NaN for ports
    without a line model) and the backend's run time in seconds. backend 'auto':
    see resolve_backend. wideband: the frequency (Hz) the stackup's er and tan d
    hold at for the Djordjevic-Sarkar dielectric (a named laminate's layer at
    its own), or None (constant er and tan d); the solves at the materials'
    frequencies go in info['held'] (Hz), when given."""
    import time
    if backend == 'auto':
        backend, _ = resolve_backend('auto', qucsator)
    if backend not in BACKENDS:
        raise ValueError(f"fast solver: choose one of {', '.join(BACKENDS)}")
    if all(p.name not in lm.ports for p in ports):
        raise ValueError(
            "no port has a line model (ports must sit on routed traces or pads)"
        )
    t0 = time.time()
    one = ((lambda m: _skrf(m, ports, freq, sheet, sigma)) if backend == 'skrf'
           else (lambda m: _qucsator(m, ports, freq, sheet, sigma, qucsator)))
    from ...lines import dispersion
    held = dispersion.nodes(lm, freq, wideband) if wideband else None
    if held is None:
        S, order = one(lm)
    else:
        runs = [one(dispersion.frozen(lm, fk, wideband)) for fk in held]
        order = runs[0][1]
        S = dispersion.interpolate(freq, held, [r[0] for r in runs])
        if info is not None:
            info['held'] = [float(x) for x in held]
    seconds = time.time() - t0
    names = [p.name for p in ports]
    full = np.full((len(freq), len(names), len(names)), np.nan + 0j)
    idx = [names.index(n) for n in order]
    comp = _components(lm)
    # (a part port: its first node)
    at = lambda n: lm.ports[n][0] if isinstance(lm.ports[n], tuple
                                                ) else lm.ports[n]
    for a, i in enumerate(idx):
        for b, j in enumerate(idx):
            # no path between them: unknown
            if comp.get(at(order[a])) == comp.get(at(order[b])):
                full[:, i, j] = S[:, a, b]
    return full, seconds


def _components(lm):
    """Connected part of the line model each node belongs to (through any
    element except ground, and across an end gap: its series capacitance joins
    the two sides)."""
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
    for j in lm.junctions:
        if j['kind'] == 'gap':
            parent[find(j['node'])] = find(j['other']['node'])
    return {n: find(n) for n in list(parent)}
