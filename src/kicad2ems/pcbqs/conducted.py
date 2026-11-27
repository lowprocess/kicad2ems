"""
pcbqs.conducted - the conducted emission of a switching circuit as a CISPR test
measures it: the circuit (pcbqs.circuit, ngspice, on its board's copper when
asked) fed through a line impedance stabilisation network (LISN, an artificial
network) in each supply line, the voltage at the networks' EUT terminals taken
over a steady period to its spectrum, read by a CISPR 16-1-1 receiver (peak,
quasi-peak and average detectors) and set against CISPR 32's or CISPR 25's
limits.

    python -m kicad2ems.pcbqs.conducted buck.json --standard cispr32 \\
        --cispr-class B --out emc
    python -m kicad2ems.pcbqs.conducted buck.json --standard cispr25 \\
        --cispr-class 5 --cm-cap sw=15p --copper --out emc25

    from kicad2ems.pcbqs import conducted
    res = conducted.run(model, 'emc', standard='cispr32', klass='B')
    res['cases'][0]['margins']   # per band and detector: the worst line's dB

The networks (one per supply line, the model's DC source moved behind them):

  cispr16  the 50 ohm / 50 uH + 5 ohm V-network of CISPR 16-1-2 (9 kHz - 30
           MHz; CISPR 32's): its impedance at the EUT terminal 50 || (5 + j w
           50 uH). Realised as 50 uH from the EUT terminal to a node held to the
           reference by 5 ohm in series with 8 uF and fed from the supply
           through 250 uH, and the receiver's 50 ohm behind 1 uF: within 0.9 %
           of the standard's impedance from 150 kHz to 30 MHz, 15 % at 9 kHz
           (the standard allows 20 %).
  cispr25  the 5 uH / 50 ohm artificial network of CISPR 25 (0.15 - 108 MHz): 5
           uH from the supply to the EUT terminal, 1 uF to the reference on the
           supply side, the receiver's 50 ohm (and 1 kohm) behind 0.1 uF: 50 ||
           j w 5 uH within 5 %.

The source's minus is the reference (the ground plane the networks stand on);
the return line has its network too (`--lines +` leaves it out: then the board's
return is the reference, and there is no common mode). The disturbance voltage
reported is the EUT terminal's against the reference, as the receiver's reading
corrected by the network's voltage-division factor gives it. Each line's (L+,
L-) is read; the differential mode is (V+ - V-) / 2, the common mode (V+ + V-)
/ 2.

The common mode needs a path from the circuit to the reference: each model
node's copper has a capacitance to a plane `--height-mm` below the board
(CISPR 25: 50 mm, the EUT on its support; CISPR 32: 400 mm, its distance to the
vertical reference plane), from pcbqs's electrostatic kernel (the nets' copper
meshed, the plane as images; the board's dielectric left out: it changes a
capacitance to a plane that far little), put in as capacitors to the reference;
and `--cm-cap NODE=VALUE` adds one of the user's (a heatsink on the switch node,
a chassis-mounted part's tab).

The receiver (CISPR 16-1-1), its band by frequency:

    band A   9 - 150 kHz    RBW 200 Hz   QP charge 45 ms, discharge 500 ms,
                                         meter 160 ms
    band B   0.15 - 30 MHz  RBW 9 kHz    QP 1 ms, 160 ms, meter 160 ms
    band C   30 - 1000 MHz  RBW 120 kHz  QP 1 ms, 550 ms, meter 100 ms

The steady state is periodic, so the voltage is a line spectrum at multiples of
1 / period. Tuned to f, the IF filter (Gaussian, the RBW its 6 dB width) passes
sum_k a_k H(f_k - f) e^{j 2 pi f_k t}, exactly, in the steady state; its
envelope over one period gives the peak (its largest), the average (its mean)
and the quasi-peak (the detector's charge and discharge run on the envelope to
their own periodic state, read by the critically damped meter, scaled so a
constant envelope reads itself), each as the RMS of a sine (/ sqrt 2), as a
receiver indicates. Lines further
apart than twice the RBW are read one at a time, and all three detectors agree;
closer lines (a period of many switching cycles, a burst) beat in the filter and
the detectors part. The receiver is tuned at every line and, where lines are
closer than 2 RBW, every quarter RBW. What it leaves out: the IF filter's shape
beyond its 6 dB width (CISPR's mask allows a range; a Gaussian is taken), its
settling (the input is periodic, so none is needed), anything not periodic in
the circuit's period (spread-spectrum, noise).

Limits: CISPR 32's AC mains port, class A or B (quasi-peak and average, dBuV);
CISPR 25's conducted voltage method, classes 1 to 5 (peak, quasi-peak and
average in its broadcast and mobile bands). The values are the tool's
transcription (limits() below): check them against the edition your test plan
names, or give your own (`--limits FILE`: lines of detector, f1_Hz, f2_Hz,
dBuV1, dBuV2; log-linear between f1 and f2). The margin of a band is the limit
minus the reading at its worst frequency, per detector, of the worse line.

The run: the circuit alone first (<out>/alone, no copper, no strict reported
run: its state is all that is wanted), then with the networks started where it
settled (their inductors at its input current, their capacitors at its DC;
their own states not judged for repeating: their modes, 2-4 kHz, are far below
the band) and reported at needed_samples' steps (<out>/circuit).

<out>/conducted.json: the standard, network, the capacitances put in, and per
case the margins and the worst frequencies; <out>/circuit/<case>/conducted.csv
the readings (f, band, detector, L+, L-, DM, CM, limit); <out>/conducted.png
(the first case's readings with the limit lines).
"""
import argparse
import copy
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

FORMAT = 1
REFERENCE = 'emc_ref'          # the reference ground's node (the ground plane)
SUPPLY = 'emc_supply'          # the supply's + terminal behind its network

# (L1 series to the EUT terminal, the mains side, the receiver branch)
NETWORKS = dict(
    cispr16=dict(
        label='50 ohm / 50 uH + 5 ohm V-network (CISPR 16-1-2)',
        L1=50e-6, R1=5.0, C1=8e-6, L2=250e-6, C2=1e-6, R_rx=50.0,
        R_discharge=None, band=(9e3, 30e6)),
    cispr25=dict(
        label='5 uH / 50 ohm artificial network (CISPR 25)',
        L1=5e-6, C1=1e-6, C2=0.1e-6, R_rx=50.0, R_discharge=1e3,
        band=(150e3, 108e6)),
)

# CISPR 16-1-1's receiver bands: (name, f low, f high, RBW (6 dB), QP charge,
# QP discharge, the meter's time constant) in Hz and s
BANDS = (('A', 9e3, 150e3, 200.0, 45e-3, 500e-3, 160e-3),
         ('B', 150e3, 30e6, 9e3, 1e-3, 160e-3, 160e-3),
         ('C', 30e6, 1e9, 120e3, 1e-3, 550e-3, 100e-3))
DETECTORS = ('peak', 'qp', 'avg')


def _cispr25(cls):
    """CISPR 25's conducted voltage-method limits (dBuV) of a class 1-5: its
    bands, each (name, f1, f2, peak, qp, avg) at class 5 and the step from one
    class to the next."""
    k = 5 - int(cls)
    rows = []
    for name, f1, f2, pk, qp, av, step in (
            ('LW', 0.15e6, 0.30e6, 70, 57, 50, 10),
            ('MW', 0.53e6, 1.8e6, 54, 41, 34, 8),
            ('SW', 5.9e6, 6.2e6, 53, 40, 33, 6),
            ('CB', 26e6, 28e6, 44, 31, 24, 6),
            ('VHF', 30e6, 54e6, 44, 31, 24, 6),
            ('VHF', 68e6, 87e6, 39, 26, 19, 6),
            ('FM', 76e6, 108e6, 38, 25, 18, 6)):
        rows.append((name, f1, f2, pk + k * step, qp + k * step,
                     av + k * step))
    out = dict(peak=[], qp=[], avg=[])
    for name, f1, f2, pk, qp, av in rows:
        for det, v in (('peak', pk), ('qp', qp), ('avg', av)):
            out[det].append((name, f1, f2, v, v))
    return out


def limits(standard, klass):
    """{detector: [(band name, f1, f2, dBuV at f1, dBuV at f2)]}: log-linear in
    f between f1 and f2, nothing outside the bands."""
    s, c = str(standard).lower(), str(klass).upper()
    if s == 'cispr32':
        if c == 'A':
            return dict(qp=[('0.15-0.5 MHz', 0.15e6, 0.5e6, 79, 79),
                            ('0.5-30 MHz', 0.5e6, 30e6, 73, 73)],
                        avg=[('0.15-0.5 MHz', 0.15e6, 0.5e6, 66, 66),
                             ('0.5-30 MHz', 0.5e6, 30e6, 60, 60)])
        if c == 'B':
            return dict(qp=[('0.15-0.5 MHz', 0.15e6, 0.5e6, 66, 56),
                            ('0.5-5 MHz', 0.5e6, 5e6, 56, 56),
                            ('5-30 MHz', 5e6, 30e6, 60, 60)],
                        avg=[('0.15-0.5 MHz', 0.15e6, 0.5e6, 56, 46),
                             ('0.5-5 MHz', 0.5e6, 5e6, 46, 46),
                             ('5-30 MHz', 5e6, 30e6, 50, 50)])
        raise ValueError(f"CISPR 32: class A or B (got {klass!r})")
    if s == 'cispr25':
        if c not in '12345' or len(c) != 1:
            raise ValueError(f"CISPR 25: class 1 to 5 (got {klass!r})")
        return _cispr25(c)
    raise ValueError(f"standard: cispr32 or cispr25 (got {standard!r})")


def read_limits(path):
    """A limits file: lines of detector (peak, qp, avg), f1_Hz, f2_Hz, dBuV1[,
    dBuV2]; # comments and a header skipped. ValueError says what is wrong."""
    out = dict(peak=[], qp=[], avg=[])
    with open(path, newline='', encoding='utf-8') as fh:
        for k, line in enumerate(csv.reader(fh)):
            cells = [c.strip() for c in line if c.strip()]
            if not cells or cells[0].startswith('#'):
                continue
            det = cells[0].lower()
            if det not in out:
                if k == 0:
                    continue
                raise ValueError(
                    f"{path}: line {k + 1}: the detector is peak, qp or avg")
            try:
                v = [float(x) for x in cells[1:5]]
            except ValueError:
                raise ValueError(
                    f"{path}: line {k + 1} is not numbers") from None
            if len(v) < 3 or not 0 < v[0] < v[1]:
                raise ValueError(
                    f"{path}: line {k + 1}: f1_Hz < f2_Hz and a limit")
            out[det].append((f"{v[0] / 1e6:g}-{v[1] / 1e6:g} MHz", v[0], v[1],
                             v[2], v[3] if len(v) > 3 else v[2]))
    if not any(out.values()):
        raise ValueError(f"{path}: no limits")
    return out


def limit_at(rows, f):
    """The limit (dBuV) at frequencies f from one detector's rows; NaN outside
    them. Where two rows meet the lower applies (CISPR: the lower limit at a
    transition frequency)."""
    f = np.asarray(f, float)
    out = np.full(f.shape, np.nan)
    for _, f1, f2, v1, v2 in rows:
        inside = (f >= f1 * (1 - 1e-9)) & (f <= f2 * (1 + 1e-9))
        v = v1 + (v2 - v1) * np.log(f[inside] / f1) / math.log(f2 / f1)
        out[inside] = np.fmin(out[inside], v)  # NaN-aware: the lower
    return out


# ---- the networks ----

def impedance(network, f, source_ohm=0.0):
    """The network's impedance at its EUT terminal (ohm), its supply an ideal
    source behind source_ohm."""
    n = NETWORKS[network]
    w = 2 * np.pi * np.asarray(f, float)
    par = lambda a, b: a * b / (a + b)  # noqa: E731
    rx = n['R_rx'] if not n['R_discharge'] else par(n['R_rx'],
                                                     n['R_discharge'])
    branch = rx + 1 / (1j * w * n['C2'])
    if 'L2' in n:
        mains = par(n['R1'] + 1 / (1j * w * n['C1']),
                    1j * w * n['L2'] + source_ohm)
    else:
        mains = par(1 / (1j * w * n['C1']), source_ohm + 1e-30)
    return par(branch, 1j * w * n['L1'] + mains)


def standard_impedance(network, f):
    """The impedance the standard gives the network: 50 || (5 + j w 50 uH)
    (CISPR 16-1-2's), 50 || j w 5 uH (CISPR 25's)."""
    w = 2 * np.pi * np.asarray(f, float)
    z = (5 + 1j * w * 50e-6) if network == 'cispr16' else 1j * w * 5e-6
    return 50 * z / (50 + z)


def find_source(model, ref=None):
    """The model's DC voltage supply: the part named, or its one source with
    dc_V. ValueError: none, or several and none named."""
    live = [p for p in model['parts'] if p.get('enabled', True)]
    if ref:
        hit = [p for p in live if p['ref'].lower() == str(ref).lower()]
        if not hit or hit[0]['type'] != 'source' or 'dc_V' not in hit[0]:
            raise ValueError(f"--source {ref}: not a DC voltage source (dc_V) "
                             "of the model")
        return hit[0]
    found = [p for p in live if p['type'] == 'source' and 'dc_V' in p]
    if not found:
        raise ValueError('the model has no DC voltage source (dc_V): the '
                         'supply the networks go in')
    if len(found) > 1:
        raise ValueError(
            "the model has several DC sources: name the supply with --source "
            f"({', '.join(p['ref'] for p in found)})")
    return found[0]


def insert(model, network='cispr16', source=None, lines=('+', '-'), cm=()):
    """(the model with a network in each supply line, what was put in). The
    source's + terminal is fed through its network to the node it was on (the
    EUT terminal); its - terminal is the reference, the return's network
    between it and the node it was on (lines: '+' alone leaves that out, the
    return then the reference itself). cm: [(node, farads)] capacitors from
    model nodes to the reference. On a board, the networks' EUT ends take the
    source's own footprint (its pins' pads), the source itself goes off the
    board."""
    if network not in NETWORKS:
        raise ValueError(f"network: {', '.join(NETWORKS)} (got {network!r})")
    lines = tuple(lines)
    if not lines or set(lines) - {'+', '-'} or lines[0] != '+':
        raise ValueError("lines: '+' or '+,-'")
    m = copy.deepcopy(model)
    src = find_source(m, source)
    used = {n for p in m['parts'] for n in p['nodes']}
    taken = {p['ref'].lower() for p in m['parts']}
    for name in (REFERENCE, SUPPLY):
        if name in used:
            raise ValueError(f"the model already has a node {name!r}")
    plus, minus = src['nodes']
    board = m.get('board')
    on_board = isinstance(board, dict) and bool(board)
    from .circuit.copper import footprint_of
    # the source's footprint when board.parts names it (a footprint of its own
    # reference is not known to be on the board: the networks' ends then stand
    # at their nets' nodes)
    named = on_board and src['ref'] in (board.get('parts') or {})
    fps = ([footprint_of(m, src['ref'], k) for k in (0, 1)]
           if named else [None, None])
    ref_node = REFERENCE if '-' in lines else minus
    src['nodes'] = [SUPPLY, ref_node]
    n = NETWORKS[network]
    added, info = [], dict(network=network, label=n['label'],
                           source=src['ref'], reference=ref_node, lines=[])
    mapping = {}

    def part(ref, kind, a, b, value, pins=None):
        if ref.lower() in taken:
            raise ValueError(f"the model already has a part {ref}")
        taken.add(ref.lower())
        added.append(dict(ref=ref, type=kind, nodes=[a, b], value=value))
        if on_board:
            mapping[ref] = pins
        return ref

    for line, eut, supply, fp in (('+', plus, SUPPLY, fps[0]),
                                  ('-', minus, ref_node, fps[1])):
        if line not in lines:
            continue
        p = 'LISNP' if line == '+' else 'LISNN'
        mid, rx = f"emc_{p[-1].lower()}_mid", f"emc_{p[-1].lower()}_rx"
        refs = {}
        if 'L2' in n:
            refs['L2'] = part(p + '_L2', 'inductor', supply, mid, n['L2'])
            k = f"emc_{p[-1].lower()}_c1"
            refs['R1'] = part(p + '_R1', 'resistor', mid, k, n['R1'])
            refs['C1'] = part(p + '_C1', 'capacitor', k, ref_node, n['C1'])
            start = mid
        else:
            if supply != ref_node:
                refs['C1'] = part(p + '_C1', 'capacitor', supply, ref_node,
                                  n['C1'])
            start = supply
        refs['L1'] = part(p + '_L1', 'inductor', start, eut, n['L1'],
                          [None, fp])
        refs['C2'] = part(p + '_C2', 'capacitor', eut, rx, n['C2'],
                          [fp, None])
        refs['RX'] = part(p + '_RX', 'resistor', rx, ref_node, n['R_rx'])
        if n['R_discharge']:
            refs['RD'] = part(p + '_RD', 'resistor', rx, ref_node,
                              n['R_discharge'])
        info['lines'].append(dict(line=line, eut=eut, parts=refs))
    info['cm'] = []
    if '-' in lines:
        for i, (node, c) in enumerate(cm):
            if node not in used and node not in ('0', 'GND', 'gnd'):
                raise ValueError(f"--cm-cap {node}: no part is on that node")
            r = part(f"EMC_CM{i + 1}", 'capacitor', node, REFERENCE, float(c))
            info['cm'].append(dict(ref=r, node=node, C_F=float(c)))
    elif cm:
        raise ValueError("a common-mode capacitance needs the return's network "
                         "(--lines +,-)")
    m['parts'] += added
    if on_board:
        board.setdefault('parts', {})
        board['parts'][src['ref']] = None
        board['parts'].update(mapping)
    return m, info


# ---- the board's capacitance to the reference plane ----

def plane_capacitance(board, nets, height_mm, dmax=2.0, max_triangles=6000,
                      log=print):
    """{net: its capacitance (F) to a plane height_mm below the board's lowest
    copper}, the nets' copper meshed (pcbqs.pdn.mesh) and solved together for
    their charges (constant on each triangle, pcbqs's 1/R moments) with the
    plane as their image; the board's dielectric left out (homogeneous air).
    Nets with no copper are left out. A mesh over max_triangles is made
    coarser."""
    from .pdn.mesh import net_sheets
    nets = [n for n in dict.fromkeys(nets) if n]
    while True:
        nodes, tris, z, owner, off = [], [], [], [], 0
        for k, net in enumerate(nets):
            for sh in net_sheets(board, net, dmax=dmax).values():
                nodes.append(np.asarray(sh.nodes, float) * 1e-3)
                tris.append(np.asarray(sh.tris) + off)
                z += [sh.z * 1e-3] * len(sh.tris)
                owner += [k] * len(sh.tris)
                off += len(sh.nodes)
        count = sum(len(t) for t in tris)
        if count <= max_triangles or dmax > 50:
            break
        dmax *= 1.5
    if not count:
        return {}, dict(triangles=0, dmax_mm=dmax)
    started = time.perf_counter()
    z, owner = np.asarray(z), np.asarray(owner)
    C = capacitance_to_plane(np.vstack(nodes), np.vstack(tris), z, owner,
                             z.min() - height_mm * 1e-3, len(nets))
    log(f"conducted: the nets' capacitance to the reference plane "
        f"{height_mm:g} mm below: {count} triangles, "
        f"{time.perf_counter() - started:.2f} s")
    present = sorted(set(owner.tolist()))
    return ({nets[k]: float(C[k].sum()) for k in present},
            dict(triangles=int(count), dmax_mm=float(dmax),
                 maxwell_F=[[float(C[i, j]) for j in present]
                            for i in present],
                 nets=[nets[k] for k in present]))


def capacitance_to_plane(nodes, tris, z, owner, plane, count=None):
    """The Maxwell capacitance matrix (F) of conductors of flat triangles
    (nodes (N, 2) m, tris, each triangle's z (m) and its conductor `owner`)
    over a ground plane at z = plane, the plane and infinity the reference:
    a charge constant on each triangle, the potential its average over each
    (pcbqs's 1/R moments for the triangles' own, centroid to centroid for
    their images: the image is twice the height away)."""
    from .static import static_moments
    from kicad2ems.emsdev.numerics.base import EPS0
    from scipy.linalg import cho_factor, cho_solve
    _, M0 = static_moments(nodes, tris, z, scalar_only=True)
    v = nodes[tris]
    e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
    area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    cent = v.mean(axis=1)
    P = M0                                  # in place: one N x N array
    P /= area[:, None]
    P /= area[None, :]
    for a in range(0, len(z), 1024):        # row blocks: no N x N temporaries
        d = cent[a:a + 1024, None, :] - cent[None, :, :]
        h = z[a:a + 1024, None] + z[None, :] - 2 * plane
        P[a:a + 1024] -= 1 / np.sqrt((d ** 2).sum(-1) + h * h)
    P *= 1 / (4 * np.pi * EPS0)
    n = int(count or owner.max() + 1)
    B = np.zeros((len(z), n))
    B[np.arange(len(z)), owner] = 1.0
    C = B.T @ cho_solve(cho_factor(P, overwrite_a=True), B)
    return 0.5 * (C + C.T)


def board_cm(model, height_mm, log=print):
    """[(node, farads)] for the model's nodes from its board (plane_capacitance
    of their nets), and what was solved; ([], None) with no board."""
    from ._cli import load_board
    from .circuit.copper import net_name
    b = model.get('board') or {}
    if not b.get('file'):
        return [], None
    board = load_board(b['file'])
    known = {n.get('name') if isinstance(n, dict) else n
             for n in board.get('nets', [])} if isinstance(
                 board.get('nets'), list) else set(board.get('nets', {}) or ())
    nodes = list(dict.fromkeys(
        n for p in model['parts'] if p.get('enabled', True)
        for n in p['nodes']))
    named = {}
    for node in nodes:
        net = net_name(model, node, known or ('GND',))
        named.setdefault(net, node)
    caps, solved = plane_capacitance(board, list(named), height_mm, log=log)
    out = [(named[net], c) for net, c in caps.items() if c > 1e-15]
    return out, dict(solved or {}, nodes={named[n]: n for n in caps})


# ---- the spectrum and the receiver ----

def lines_of(t, v, period):
    """(f, complex peak amplitudes) of v over the last period of a uniformly
    sampled run: v(t) = Re sum a_k e^{j 2 pi f_k t}, f_k = k / period,
    k >= 1."""
    t, v = np.asarray(t, float), np.asarray(v, float)
    n = int(round(period / (t[1] - t[0])))
    if n < 4 or len(t) < n + 1:
        raise ValueError('the run is shorter than a period')
    last = v[len(t) - 1 - n:len(t) - 1]
    c = np.fft.rfft(last) / n
    k = np.arange(1, len(c) - 1)          # Nyquist's own left out
    return k / period, 2 * c[1:len(c) - 1]


def _gauss(df, rbw):
    """The IF filter's amplitude response: Gaussian, -6 dB (a half) at +-rbw /
    2."""
    return np.exp(-math.log(2) * (2 * df / rbw) ** 2)


def _meter(v, period, tm):
    """The indicating instrument on periodic detector outputs v (rows, samples
    of one period): critically damped, T_M^2 a'' + 2 T_M a' + a = v (CISPR
    16-1-1's mechanical time constant), in its periodic state; its largest
    deflection over the period."""
    m = v.shape[1]
    k = np.arange(m // 2 + 1)
    h = 1 / (1 + 2j * np.pi * k * tm / period) ** 2
    return np.fft.irfft(np.fft.rfft(v, axis=1) * h, n=m, axis=1).max(axis=1)


def _qp(e, period, charge, discharge, meter, iterations=40):
    """The quasi-peak detector's steady reading on periodic envelopes e (rows,
    samples of one period): charged while e is above it, discharged always,
    the two time constants CISPR 16-1-1's (charge: a sine applied, 63 % of
    the final value; discharge: removed, down to 37 %), so dv/dt = (1 / tau_c -
    1 / tau_d)(e - v) - v / tau_d while charging; its periodic state found by
    a secant on the period map; read by the meter (_meter), scaled so a
    constant envelope reads itself."""
    rows, m = e.shape
    dt = period / m
    gain = 1 - charge / discharge       # a constant e settles at gain * e
    up, down = math.exp(-dt / charge), math.exp(-dt / discharge)

    ge = np.ascontiguousarray((gain * e).T)     # a sample's row contiguous

    def period_map(v0, cols=None, keep=False):
        g_all = ge if cols is None else ge[:, cols]
        v = v0.copy()
        path = np.empty(g_all.shape) if keep else None
        for j in range(m):
            g = g_all[j]
            v = np.where(g > v, g + (v - g) * up, v * down)
            if keep:
                path[j] = v
        return v, path

    # the start: the averaged detector's state (exact for a period short
    # against the charge time constant), (1/tau_c - 1/tau_d) mean((e - v)+)
    # = v / tau_d, by bisection
    et = ge / gain
    lo, hi = np.zeros(rows), et.max(axis=0)
    ratio = (1 / charge - 1 / discharge) * discharge
    for _ in range(16):         # to 2^-16 of the bracket: the secant ends it
        mid = 0.5 * (lo + hi)
        gap = et - mid
        np.maximum(gap, 0, out=gap)
        over = ratio * gap.mean(axis=0) > mid
        lo, hi = np.where(over, mid, lo), np.where(over, hi, mid)
    del et, gap
    a = 0.5 * (lo + hi)
    fa, _ = period_map(a)
    b = fa.copy()
    # each row's fixed point by a secant on the period map (monotone, a
    # contraction), the rows that have settled left out of the next steps
    active = np.arange(rows)
    for _ in range(iterations):
        fb, _ = period_map(b[active], active)
        ba, aa, faa = b[active], a[active], fa[active]
        done = np.abs(fb - ba) <= 1e-4 * np.maximum(np.abs(ba), 1e-30)
        step = ba - aa
        slope = np.clip(np.where(np.abs(step) > 1e-12 * np.abs(ba),
                                 (fb - faa) / np.where(step != 0, step, 1),
                                 0.0), 0.0, 0.999)
        a[active], fa[active] = ba, fb
        b[active] = np.where(done, ba, ba + (fb - ba) / (1 - slope))
        active = active[~done]
        if not len(active):
            break
    _, path = period_map(b, keep=True)
    return _meter(path.T, period, meter) / gain


def receive(f, a, period, fmin, fmax, oversample=8, qp_oversample=4,
            samples=1 << 21):
    """The receiver's readings (V, as the RMS of a sine) over [fmin, fmax] of a
    periodic signal's lines a at f: (tuned frequencies, {detector: readings},
    band names). Each band of BANDS in turn: tuned at every line, and every
    quarter RBW where lines are closer than 2 RBW. The envelope of a period is
    sampled at `oversample` times its lines in the filter for the peak and the
    average, `qp_oversample` times for the quasi-peak (its detector steps
    through it sample by sample); `samples` bounds a batch's envelopes."""
    f, a = np.asarray(f, float), np.asarray(a, complex)
    df = 1 / period
    out_f, out_band = [], []
    out = {d: [] for d in DETECTORS}
    for name, lo, hi, rbw, tc, td, tm in BANDS:
        lo, hi = max(lo, fmin), min(hi, fmax)
        if lo >= hi:
            continue
        k_line = np.flatnonzero((f >= lo) & (f <= hi))
        tuned = f[k_line]
        if df < 2 * rbw:
            tuned = np.union1d(tuned, np.arange(lo, hi, rbw / 4))
        if not len(tuned):
            continue
        half = int(math.ceil(3 * rbw / df)) + 1
        width = 2 * half + 1
        m = 1 << max(3, int(math.ceil(math.log2(oversample * width))))
        mq = min(m, 1 << max(4, int(math.ceil(math.log2(qp_oversample
                                                         * width)))))
        k0 = np.rint(tuned / df).astype(int)
        batch = int(min(8192, max(64, samples // m)))
        for s in range(0, len(tuned), batch):
            ft, kc = tuned[s:s + batch], k0[s:s + batch]
            ks = kc[:, None] + np.arange(-half, half + 1)[None, :]
            ok = (ks >= 1) & (ks <= len(f))
            c = np.where(ok, a[np.clip(ks - 1, 0, len(f) - 1)], 0) * _gauss(
                ks * df - ft[:, None], rbw)
            mag = np.abs(c)
            top = mag.max(axis=1)
            second = np.sort(mag, axis=1)[:, -2] if width > 1 else 0 * top
            alone = second <= 1e-6 * np.maximum(top, 1e-300)
            pk, qp, av = top.copy(), top.copy(), top.copy()
            many = np.flatnonzero(~alone)
            if len(many):
                spec = np.zeros((len(many), m), complex)
                spec[:, :width] = c[many]
                env = np.abs(np.fft.ifft(spec, axis=1)) * m
                pk[many] = env.max(axis=1)
                av[many] = env.mean(axis=1)
                if mq < m:
                    env = np.abs(np.fft.ifft(spec[:, :mq], axis=1)) * mq
                qp[many] = _qp(env, period, tc, td, tm)
                del spec, env
            for d, v in zip(DETECTORS, (pk, qp, av)):
                out[d].append(v / math.sqrt(2))
            out_f.append(ft)
            out_band += [name] * len(ft)
    if not out_f:
        return np.zeros(0), {d: np.zeros(0) for d in DETECTORS}, []
    return (np.concatenate(out_f),
            {d: np.concatenate(v) for d, v in out.items()}, out_band)


FLOOR_DBUV = -60.0


def dbuv(v):
    """V (RMS) to dBuV, no lower than FLOOR_DBUV (1 nV: nothing)."""
    v = np.maximum(np.asarray(v, float), 1e-6 * 10 ** (FLOOR_DBUV / 20))
    return 20 * np.log10(v / 1e-6)


def margins(f, readings, lim):
    """Per limit row and detector: the limit minus the reading at the worst
    frequency inside it (dB; negative: over the limit), that frequency, the
    reading and the limit there. readings: {detector: dBuV}."""
    out = []
    for det, rows in lim.items():
        if det not in readings:
            continue
        for name, f1, f2, v1, v2 in rows:
            inside = np.flatnonzero((f >= f1 * (1 - 1e-9))
                                    & (f <= f2 * (1 + 1e-9)))
            if not len(inside):
                continue
            # the band's own limit (where bands overlap, as CISPR 25's FM and
            # VHF, each is judged against its own)
            ref = limit_at([(name, f1, f2, v1, v2)], f[inside])
            gap = ref - readings[det][inside]
            j = int(np.nanargmin(gap))
            out.append(dict(band=name, detector=det, f_Hz=float(f[inside][j]),
                            reading_dBuV=float(readings[det][inside][j]),
                            limit_dBuV=float(ref[j]),
                            margin_dB=float(gap[j])))
    return out


# ---- the run ----

def needed_samples(period, fmax, existing=None):
    """Samples a period for the reported run: the spectrum to fmax at 8 points
    a cycle of its top line, a power of two, at least what the model asks."""
    n = 1 << int(math.ceil(math.log2(max(8 * fmax * period, 256))))
    return int(max(n, existing or 0))


def case_readings(folder, info, period, fmin, fmax, lim):
    """A case's spectrum, readings and margins from its circuit folder."""
    with np.load(Path(folder) / 'transient.npz') as z:
        t = z['time_s']
        volts = {}
        for row in info['lines']:
            p = row['parts']
            volts[row['line']] = z[p['C2'] + '.V'] + z[p['RX'] + '.V']
    f, plus = lines_of(t, volts['+'], period)
    notes = []
    # read no closer to the run's Nyquist frequency than a factor 2 (a run of
    # fewer samples than needed_samples asks: the top of its spectrum is
    # folded back)
    top = 0.25 / (t[1] - t[0])
    if fmax > top:
        notes.append(f"read to {top / 1e6:.4g} MHz, not {fmax / 1e6:.4g}: the "
                     "reported run's steps were too coarse for the rest")
        fmax = top
    spectra = {'L+': plus}
    if '-' in volts:
        _, minus = lines_of(t, volts['-'], period)
        spectra.update({'L-': minus, 'DM': (plus - minus) / 2,
                        'CM': (plus + minus) / 2})
    read = {}
    tuned = None
    for name, a in spectra.items():
        tuned, r, bands = receive(f, a, period, fmin, fmax)
        read[name] = {d: dbuv(v) for d, v in r.items()}
    lines = [k for k in ('L+', 'L-') if k in read]
    worst = {d: np.max([read[k][d] for k in lines], axis=0) for d in DETECTORS}
    return dict(f=tuned, read=read, worst=worst, bands=bands,
                margins=margins(tuned, worst, lim), notes=notes,
                spectrum=dict(f=f, **spectra))


def write_csv(path, res, lim):
    names = list(res['read'])
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['f_Hz', 'band', 'detector'] + [f"{n}_dBuV" for n in names]
                   + ['limit_dBuV'])
        for det in DETECTORS:
            ref = limit_at(lim.get(det, []), res['f'])
            for i, f in enumerate(res['f']):
                w.writerow([f"{f:.9g}", res['bands'][i], det] + [
                    f"{res['read'][n][det][i]:.2f}" for n in names
                ] + ['' if np.isnan(ref[i]) else f"{ref[i]:.2f}"])


def figure(path, res, lim, title):
    """The readings of the worse line (peak as stems, QP and average as
    points: a scan between lines reads little more than the filter's skirt)
    and the limit lines; the common and differential modes' QP faint
    behind."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(9, 5), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    f = res['f'] / 1e6
    order = np.argsort(f)
    f = f[order]
    colours = dict(peak='#1f77b4', qp='#d62728', avg='#2ca02c')
    finite = np.concatenate([v[np.isfinite(v)] for v in res['worst'].values()]
                            or [np.zeros(1)])
    top = max([finite.max() if len(finite) else 0]
              + [r[3] for rows in lim.values() for r in rows]) + 10
    floor = max(top - 100, -20)
    for mode, mark in (('CM', 'v'), ('DM', '^')):
        if mode in res['read']:
            ax.plot(f, res['read'][mode]['qp'][order], mark, color='0.6',
                    ms=2.5, label=f"{mode} (QP)")
    peak = res['worst']['peak'][order]
    ok = np.isfinite(peak) & (peak > floor)
    ax.vlines(f[ok], floor, peak[ok], color=colours['peak'], lw=0.7,
              label='peak (the worse line)')
    for det in ('qp', 'avg'):
        ax.plot(f, res['worst'][det][order], '.', color=colours[det], ms=3,
                label=f"{'QP' if det == 'qp' else 'average'} (the worse line)")
    for det, rows in lim.items():
        for k, (name, f1, f2, v1, v2) in enumerate(rows):
            ax.plot([f1 / 1e6, f2 / 1e6], [v1, v2], '-', color=colours[det],
                    lw=2.2, alpha=0.55,
                    label=f"{det.upper() if det == 'qp' else det} limit"
                    if k == 0 else None)
    ax.set_xscale('log')
    ax.set_xlabel('frequency, MHz')
    ax.set_ylabel('dBuV')
    ax.set_ylim(floor, top)
    ax.grid(True, which='both', alpha=0.3)
    ax.legend(fontsize=7, loc='lower left', ncol=2)
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path)


def parse_caps(items):
    """[(node, farads)] from NODE=VALUE items (15p, 2.2e-11)."""
    from .circuit.model import number
    out = []
    for item in items or []:
        node, _, value = str(item).partition('=')
        node, value = node.strip(), value.strip()
        if not node or not value:
            raise ValueError(f"--cm-cap {item!r}: write NODE=VALUE (sw=15p)")
        c = number(value)
        if not (isinstance(c, (int, float)) and c > 0):
            raise ValueError(f"--cm-cap {item!r}: a capacitance above 0")
        out.append((node, float(c)))
    return out


def network_state(info, row, emc):
    """The networks' and the common-mode capacitors' starting state (as
    engine.end_state gives a part's) from a run of the circuit alone (its
    case row and folder): the supply's average current through the series
    inductors (back through the return's), the capacitors at their DC
    voltages, each model node's its mean over the run's last period."""
    parts = {p['ref']: p for p in emc['parts']}
    src = row['parts'][info['source']]
    amps, volts = float(src['avg_A']), float(src['avg_V'])
    with np.load(Path(row['folder']) / 'transient.npz') as z:
        t = z['time_s']
        last = t >= t[-1] - row['period_s']
        mean = {k[5:]: float(z[k][last].mean())
                for k in z.files if k.startswith('node.')}
    out = {}
    for line in info['lines']:
        sign = 1.0 if line['line'] == '+' else -1.0
        dc = volts if line['line'] == '+' else 0.0
        for ref in line['parts'].values():
            kind = parts[ref]['type']
            if kind == 'inductor':
                out[ref] = dict(initial_A=sign * amps)
            elif kind == 'capacitor':   # each from its line to the reference
                out[ref] = dict(initial_V=dc, initial_I=0.0)
    for c in info['cm']:
        out[c['ref']] = dict(initial_V=mean.get(c['node'], 0.0),
                             initial_I=0.0)
    return out


def run(model, out, standard='cispr32', klass='B', network=None, source=None,
        lines=('+', '-'), cm_caps=(), cm_board=True, height_mm=None,
        limits_file=None, fmin=None, fmax=None, copper=False, jobs=1,
        warm_start=True, log=print):
    """The conducted emission of a circuit model (model.load's): the circuit
    with the networks run to its steady state (pcbqs.circuit; warm_start: from
    the circuit alone's, network_state), each case read and set against the
    limits. Returns <out>/conducted.json's dict."""
    from .circuit import model as M
    from .circuit.engine import run as run_circuit
    started = time.perf_counter()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = str(standard).lower()
    lim = read_limits(limits_file) if limits_file else limits(s, klass)
    network = network or ('cispr25' if s == 'cispr25' else 'cispr16')
    if height_mm is None:
        height_mm = 50.0 if s == 'cispr25' else 400.0
    lo, hi = NETWORKS[network]['band']
    every = [r for rows in lim.values() for r in rows]
    fmin = float(fmin or max(lo, min(r[1] for r in every)))
    fmax = float(fmax or min(hi, max(r[2] for r in every)))
    if not fmin < fmax:
        raise ValueError('fmin must be below fmax')
    M.validate(model)
    lines = tuple(lines)
    caps, solved = [], None
    if cm_board and '-' in lines:
        try:
            caps, solved = board_cm(model, height_mm, log)
        except (OSError, ValueError, RuntimeError) as exc:
            log(f"conducted: the board's capacitance to the reference was not "
                f"found ({exc}): only --cm-cap's are in")
    caps = caps + list(cm_caps)
    emc, info = insert(model, network, source, lines, caps)
    from .circuit.deck import period_of
    period = period_of(M.resolve(emc))
    a = emc.setdefault('analysis', {})
    a['final_samples_per_cycle'] = needed_samples(
        period, fmax, a.get('final_samples_per_cycle'))
    a['final_always'] = True       # the spectrum is the reported run's
    # the networks' own states and the common-mode capacitors' are not judged
    # for repeating: their slow modes (L2 and C1, 2-4 kHz) are far below the
    # band and move a line by little more than the solver's own error (the
    # buck: 0.016 dB at most, 1400 periods to 600); a return-line capacitor's
    # swing is near nothing, so the engine's relative test is ill-posed on it.
    # The board's states are judged as ever, its input capacitor with them
    a['ignore'] = list(a.get('ignore', ())) + [
        r for line in info['lines'] for r in line['parts'].values()] + [
        c['ref'] for c in info['cm']]
    (out / 'emc-model.json').write_text(json.dumps(emc, indent=1))
    log(f"conducted: {info['label']} in "
        f"{' and '.join('L' + x for x in lines)}; {len(info['cm'])} "
        f"capacitance(s) to the reference; reported run "
        f"{a['final_samples_per_cycle']} samples a period "
        f"({period * 1e6:.4g} us)")
    starts = {}
    if warm_start:
        # the circuit alone first (no copper: it moves the operating point
        # little), each case's networks then started where it settled: their
        # inductors carrying its input current, their capacitors at its DC
        log("conducted: the circuit alone first, for where the networks start")
        alone = copy.deepcopy(model)
        # its state is all that is wanted of it: no strict reported run
        alone.setdefault('analysis', {})['strict'] = False
        bare = run_circuit(alone, out / 'alone', jobs=jobs, log=log)
        for row in bare['cases']:
            try:
                starts[row['name']] = dict(
                    state={**row['end_state'],
                           **network_state(info, row, emc)},
                    knobs=row['knobs'])
            except (KeyError, OSError, ValueError, TypeError) as exc:
                log(f"conducted: {row['name']}: from the DC start ({exc})")
    summary = run_circuit(emc, out / 'circuit', jobs=jobs, log=log,
                          copper=copper, starts=starts)
    cases, failed = [], list(summary['failed'])
    for row in summary['cases']:
        try:
            res = case_readings(row['folder'], info, row['period_s'], fmin,
                                fmax, lim)
        except (OSError, KeyError, ValueError) as exc:
            failed.append(dict(name=row['name'], error=f"readings: {exc}"))
            continue
        folder = Path(row['folder'])
        write_csv(folder / 'conducted.csv', res, lim)
        worst = min(res['margins'], key=lambda r: r['margin_dB'],
                    default=None)
        spec = res['spectrum']
        top = {}
        for name in ('L+', 'L-', 'DM', 'CM'):
            if name in spec:
                k = int(np.argmax(np.abs(spec[name])))
                top[name] = dict(f_Hz=float(spec['f'][k]),
                                 dBuV=float(dbuv(np.abs(spec[name][k])
                                                 / math.sqrt(2))))
        case = dict(
            name=row['name'], sweep=row.get('sweep', {}), folder=str(folder),
            period_s=row['period_s'], margins=res['margins'], worst=worst,
            passed=bool(worst is None or worst['margin_dB'] >= 0),
            largest_line=top, csv='conducted.csv',
            notes=row.get('notes', []) + res['notes'],
            efficiency=row.get('efficiency'),
            source_power_W=row.get('source_power_W'))
        cases.append(case)
        if len(cases) == 1:
            try:
                figure(out / 'conducted.png', res, lim,
                       f"{model.get('name') or 'circuit'}: "
                       f"{s.upper()} class {klass} ({row['name']})")
            # (a picture is not the result)
            except Exception as exc:  # noqa: BLE001
                case['notes'] = case['notes'] + [
                    f"conducted.png was not drawn: {exc}"]
        log(f"conducted: {row['name']}: " + (
            f"worst margin {worst['margin_dB']:+.1f} dB ({worst['detector']} "
            f"at {worst['f_Hz'] / 1e6:.4g} MHz, {worst['band']})"
            if worst else 'no reading inside the limits\' bands'))
    result = dict(
        format=FORMAT, standard=s, klass=str(klass), network=network,
        network_label=info['label'], lines=list(lines), height_mm=height_mm,
        fmin_Hz=fmin, fmax_Hz=fmax, source=info['source'],
        limits={d: [list(r) for r in rows] for d, rows in lim.items()},
        limits_file=str(limits_file) if limits_file else None,
        cm=info['cm'], cm_board=solved, cases=cases, failed=failed,
        circuit=str(out / 'circuit'), runtime_s=time.perf_counter() - started)
    (out / 'conducted.json').write_text(json.dumps(result, indent=1))
    return result


# ---- the command line ----

def _options():
    from kicad2ems.core.options import table
    return table([
        ('standard', 'str', 'cispr32',
         "the limits: cispr32 (AC mains port, class A or B) or cispr25 (the "
         "voltage method, class 1-5)", 'Standard', ('cispr32', 'cispr25')),
        ('cispr_class', 'str', 'B',
         "the class: A, B (CISPR 32); 1-5 (CISPR 25)", 'Standard'),
        ('limits', 'str', None,
         "a limits file of your own instead (detector, f1_Hz, f2_Hz, dBuV1, "
         "dBuV2 a line)", 'Standard'),
        ('network', 'str', None,
         "the network in each line: cispr16 (50 ohm / 50 uH + 5 ohm) or "
         "cispr25 (5 uH / 50 ohm); default the standard's", 'Network',
         ('cispr16', 'cispr25')),
        ('source', 'str', None,
         "the model's DC supply the networks go behind (its one dc_V source "
         "by default)", 'Network'),
        ('lines', 'str', '+,-',
         "the lines with a network: '+,-' (both: the common mode too) or '+'",
         'Network'),
        ('cm_cap', 'list', [],
         "NODE=VALUE: a capacitance from a model node to the reference (a "
         "heatsink on the switch node: sw=15p) (repeat)", 'Common mode'),
        ('no_cm_board', 'bool', False,
         "leave out the model nodes' copper's capacitance to the reference "
         "plane, which the board gives when the model has one", 'Common mode'),
        ('height_mm', 'float', None,
         "the reference plane's distance below the board (default: 50 mm "
         "CISPR 25, 400 mm CISPR 32)", 'Common mode'),
        ('fmin', 'float', None, "the lowest frequency read, Hz (default: the "
         "limits')", 'Receiver'),
        ('fmax', 'float', None, "the highest frequency read, Hz (default: the "
         "limits')", 'Receiver'),
        ('copper', 'bool', False,
         "with the copper of the model's board between its parts' pads",
         'Board'),
        ('board', 'str', None,
         'the board (.kicad_pcb), when the model names none or another',
         'Board'),
        ('set', 'list', [],
         "PATH=VALUE: a value of the model set before running (repeat)",
         'Model'),
        ('jobs', 'int', 1, 'sweep points run at once', 'Run'),
    ])


OPTIONS = _options()


def describe():
    from ._cli import describe as d
    return d(OPTIONS)


def main(argv=None):
    from ._cli import add_options, print_describe
    from .circuit import model as M
    from .circuit.copper import BoardError
    ap = argparse.ArgumentParser(prog='python -m kicad2ems.pcbqs.conducted',
                                 description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('model', nargs='?', help='a circuit model (JSON, format 2)')
    ap.add_argument('--out', default='conducted-results',
                    help='the results folder (default: conducted-results)')
    add_options(ap, OPTIONS)
    ap.add_argument('--describe', action='store_true',
                    help='the options (with --json: for a front end)')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.describe:
        print_describe(describe(), a.json)
        return 0
    if not a.model:
        ap.error('a model is required')
    say = lambda s: print(s, flush=True)  # noqa: E731
    try:
        model = M.load(a.model)
        for item in a.set:
            path, _, value = item.partition('=')
            M.put(model, path.strip(),
                  (M.number(value) if not value.strip().startswith('[')
                   else json.loads(value)))
        if a.board:
            model.setdefault('board', {})['file'] = a.board
        M.validate(model)
        res = run(model, a.out, standard=a.standard, klass=a.cispr_class,
                  network=a.network, source=a.source,
                  lines=[x.strip() for x in str(a.lines).split(',')
                         if x.strip()],
                  cm_caps=parse_caps(a.cm_cap), cm_board=not a.no_cm_board,
                  height_mm=a.height_mm, limits_file=a.limits, fmin=a.fmin,
                  fmax=a.fmax, copper=a.copper, jobs=a.jobs, log=say)
    except (M.ModelError, BoardError, ValueError) as e:
        print(f"conducted: error: {e}", file=sys.stderr)
        return 2
    for case in res['cases']:
        print(f"{case['name']}: " + ('passes' if case['passed'] else 'FAILS')
              + (f", worst margin {case['worst']['margin_dB']:+.1f} dB"
                 if case['worst'] else ''), flush=True)
    for f in res['failed']:
        print(f"{f['name']}: FAILED: {f['error']}", flush=True)
    print(f"Completed conducted cases: {len(res['cases'])} of "
          f"{len(res['cases']) + len(res['failed'])}", flush=True)
    return 1 if res['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
