"""
IBIS buffers for the time-domain question: a file's models read, and each as a
port's termination of the channel's macromodel (core.macromodel.simulate).

    lib = read('part.ibs')            # {model name: Buffer data}, lib.pins
    drv = Driver(lib.model('DQ_34'), bits, ui, corner='typ')
    rx = Receiver(lib.model('IN_DQ'))
    V, I = simulate(model, [drv, rx], t)

A buffer at its die (IBIS's behavioural model, version 3.2 to 7's tables):

    I into the buffer = Ku(t) Ipu(Vcc - V) + Kd(t) Ipd(V) + Igc(V) + Ipc(Vcc - V)
                        + C_comp dV/dt

the pull-up and power clamp tables read against their references (V below the
pull-up's), each table extended linearly past its ends. Ku and Kd, the
switching, come from the edge's V-T tables: with two fixtures an edge (to
ground and to the supply) the two equations at each time give both; with one,
or [Ramp] alone (a linear 20-80 % edge into R_load, to ground rising and to the
supply falling), Kd = 1 - Ku. The package, the pin's own where [Pin] gives it,
else [Package]'s: R_pkg and L_pkg in series from the die to the pin, C_pkg at
the pin. A new edge before the last has settled starts where its own Ku is the
present one (no step in the drive).

Left out: [Driver Schedule], [Submodel], [Series Pin Mapping], EBD and
[Model Spec]'s thresholds; ECL and open-drain/source buffers' references other
than the pull-up and pull-down; C_fixture and L_fixture beyond C_fixture's
current; the waveforms' R_dut, L_dut, C_dut.
"""
import math
import re

import numpy as np

__all__ = ['IbisError', 'Library', 'Buffer', 'read', 'parse', 'number',
           'Driver', 'Receiver', 'CORNERS']

CORNERS = ('typ', 'min', 'max')
_SCALE = {'T': 1e12, 'G': 1e9, 'M': 1e6, 'k': 1e3, 'm': 1e-3, 'u': 1e-6,
          'n': 1e-9, 'p': 1e-12, 'f': 1e-15}
_NUM = re.compile(r'^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([TGMkmunpf]?)')


class IbisError(ValueError):
    pass


def number(text):
    """An IBIS number: its scale letter (T G M k m u n p f; M is mega) and
    any unit after it ignored ("1.2nH", "50ohm"); None for NA."""
    t = str(text).strip()
    if t.upper() in ('NA', ''):
        return None
    m = _NUM.match(t)
    if not m:
        raise IbisError(f"not a number: {text!r}")
    return float(m.group(1)) * _SCALE.get(m.group(2), 1.0)


def _corner(vals, corner):
    """The corner's value of a typ / min / max row (NA: typ's)."""
    k = CORNERS.index(corner)
    v = vals[k] if k < len(vals) else None
    return vals[0] if v is None else v


def _key(name):
    return re.sub(r'[\s_]+', ' ', name.strip().lower())


class Buffer:
    """A [Model]'s data: kind (Model_type), polarity, params {name: (typ,
    min, max)}, tables {'pulldown' | 'pullup' | 'gnd clamp' | 'power clamp':
    (V, I (n, 3))}, waves {'rising' | 'falling': [dict(r, v (typ, min, max),
    c, t, V (n, 3))]}, ramp {'r' | 'f': (dv, dt) per corner, 'r_load'}, refs
    {'pullup' | 'pulldown' | 'power clamp' | 'gnd clamp' | 'voltage range':
    (typ, min, max)}."""

    def __init__(self, name):
        self.name, self.kind, self.polarity = name, '', 'non-inverting'
        self.params, self.tables, self.waves, self.refs = {}, {}, {}, {}
        self.ramp = {}
        self.package = None                     # (R, L, C), its pin's or None

    def value(self, key, corner='typ', default=None):
        v = self.params.get(key)
        return default if v is None else _corner(v, corner)

    def vcc(self, corner='typ'):
        for k in ('pullup', 'voltage range'):
            if k in self.refs:
                return _corner(self.refs[k], corner)
        raise IbisError(f"model {self.name}: no [Voltage Range] or [Pullup "
                        "Reference]")

    def ref(self, which, corner='typ'):
        """A table's reference voltage: its own keyword's, else the supply
        (pull-up, power clamp) or 0 (pull-down, ground clamp)."""
        if which in self.refs:
            return _corner(self.refs[which], corner)
        return self.vcc(corner) if which in ('pullup', 'power clamp') else 0.0


class Library:
    """A file's models by name, its components' pins (pin: (signal, model,
    (R, L, C) or None)) and their [Package] (R, L, C) per corner."""

    def __init__(self):
        self.models, self.pins, self.packages, self.notes = {}, {}, {}, []

    def model(self, name, corner='typ', pin=None):
        """The model `name` (or the pin's), its package the pin's [Pin]
        values, else the component's [Package]."""
        if pin is not None:
            if pin not in self.pins:
                raise IbisError(f"no pin {pin} (pins: "
                                f"{', '.join(list(self.pins)[:12])} ...)")
            name = self.pins[pin][1]
        if name not in self.models:
            raise IbisError(f"no model {name!r} (models: "
                            f"{', '.join(self.models)})")
        b = self.models[name]
        pkg = None
        if pin is not None and self.pins[pin][2] is not None:
            pkg = self.pins[pin][2]
        elif self.packages:
            pk = next(iter(self.packages.values()))
            pkg = tuple(_corner(pk[k], corner) or 0.0
                        for k in ('r_pkg', 'l_pkg', 'c_pkg'))
        b.package = pkg
        return b


def read(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return parse(fh.read(), path)


def parse(text, name='<ibis>'):
    """The models, pins and packages of an IBIS file's text (Library)."""
    lib = Library()
    comment = '|'
    model = None
    section, rows = None, []
    comp_pins = False
    wave = None

    def flush():
        nonlocal rows
        if model is not None and section in ('pulldown', 'pullup',
                                             'gnd clamp', 'power clamp'):
            if rows:
                arr = np.array(rows, float)
                model.tables[section] = (arr[:, 0], arr[:, 1:4])
        if model is not None and wave is not None and rows:
            arr = np.array(rows, float)
            wave['t'], wave['V'] = arr[:, 0], arr[:, 1:4]
        rows = []

    for raw in text.splitlines():
        line = raw.split(comment, 1)[0].rstrip() if comment in raw else raw
        if not line.strip():
            continue
        m = re.match(r'^\s*\[([^\]]+)\]\s*(.*)$', line)
        if m:
            flush()
            key, rest = _key(m.group(1)), m.group(2).strip()
            wave, comp_pins = None, False
            section = key
            if key == 'comment char':
                comment = rest[:1] or '|'
            elif key == 'model':
                model = Buffer(rest.split()[0] if rest else '')
                lib.models[model.name] = model
            elif key == 'component':
                model = None
            elif key == 'pin':
                comp_pins = True
            elif key == 'package':
                lib.packages.setdefault('component', {})
            elif key in ('voltage range', 'pullup reference',
                         'pulldown reference', 'power clamp reference',
                         'gnd clamp reference') and model is not None:
                vals = [number(x) for x in rest.split()[:3]]
                model.refs[key.replace(' reference', '')] = vals
            elif key in ('rising waveform', 'falling waveform') and model:
                wave = dict(r=None, v=[None, None, None], c=0.0)
                model.waves.setdefault(key.split()[0], []).append(wave)
            elif key == 'end':
                model = None
            continue
        words = line.split()
        if comp_pins:
            if words[0].lower() == 'signal_name' or len(words) < 3:
                continue
            rlc = None
            if len(words) >= 6:
                rlc = tuple(number(x) or 0.0 for x in words[3:6])
            lib.pins[words[0]] = (words[1], words[2], rlc)
            continue
        if section == 'package':
            k = words[0].lower()
            if k in ('r_pkg', 'l_pkg', 'c_pkg'):
                lib.packages['component'][k] = [number(x) for x in words[1:4]]
            continue
        if model is None:
            continue
        if section == 'model':
            k = words[0].lower().rstrip('=')
            rest = line.split(None, 1)[1] if len(words) > 1 else ''
            rest = rest.lstrip('= ').strip()
            if k == 'model_type':
                model.kind = rest.lower()
            elif k == 'polarity':
                model.polarity = rest.lower()
            elif k in ('c_comp', 'vinl', 'vinh', 'vmeas', 'rref', 'cref'):
                model.params[k] = [number(x) for x in rest.split()[:3]]
            continue
        if section == 'ramp':
            k = words[0].lower()
            if k in ('dv/dt_r', 'dv/dt_f'):
                vals = []
                for x in words[1:4]:
                    if x.upper() == 'NA':
                        vals.append(None)
                        continue
                    a, _, b = x.partition('/')
                    vals.append((number(a), number(b)))
                model.ramp[k[-1]] = vals
            elif k.startswith('r_load'):
                model.ramp['r_load'] = number(line.split('=', 1)[1])
            continue
        if wave is not None:
            if '=' in line:
                for k, v in re.findall(r'(\w+)\s*=\s*(\S+)', line):
                    k = k.lower()
                    if k == 'r_fixture':
                        wave['r'] = number(v)
                    elif k == 'v_fixture':
                        wave['v'][0] = number(v)
                    elif k == 'v_fixture_min':
                        wave['v'][1] = number(v)
                    elif k == 'v_fixture_max':
                        wave['v'][2] = number(v)
                    elif k == 'c_fixture':
                        wave['c'] = number(v) or 0.0
                continue
            vals = [number(x) for x in words[:4]]
            rows.append([vals[0]] + [v if v is not None else vals[1]
                                     for v in (vals[1:4] + [None] * 3)[:3]])
            continue
        if section in ('pulldown', 'pullup', 'gnd clamp', 'power clamp'):
            vals = [number(x) for x in words[:4]]
            if vals[0] is None:
                continue
            rows.append([vals[0]] + [v if v is not None else vals[1]
                                     for v in (vals[1:4] + [None] * 3)[:3]])
    flush()
    if not lib.models:
        raise IbisError(f"{name}: no [Model]")
    return lib


# ---- a buffer at its die ----------------------------------------------------
class _Curves:
    """A buffer's I-V tables at a corner: I into the buffer and dI/dV."""

    def __init__(self, b, corner):
        k = CORNERS.index(corner)
        self.vcc = b.vcc(corner)
        self.t = {}
        for name in ('pulldown', 'pullup', 'gnd clamp', 'power clamp'):
            if name in b.tables:
                v, I = b.tables[name]
                o = np.argsort(v)
                self.t[name] = (v[o], I[o, k], b.ref(name, corner))

    def _one(self, name, x):
        if name not in self.t:
            return 0.0, 0.0
        v, I, _ = self.t[name]
        if len(v) == 1:
            return float(I[0]), 0.0
        # linear between the points, extended past the ends
        j = int(np.clip(np.searchsorted(v, x) - 1, 0, len(v) - 2))
        s = (I[j + 1] - I[j]) / (v[j + 1] - v[j])
        return float(I[j] + s * (x - v[j])), float(s)

    def parts(self, V):
        """(Ipu, dIpu/dV, Ipd, dIpd/dV, clamps, dclamps/dV) at pin voltage V
        (each into the buffer)."""
        pu, dpu = self._at('pullup', V)
        pd, dpd = self._at('pulldown', V)
        gc, dgc = self._at('gnd clamp', V)
        pc, dpc = self._at('power clamp', V)
        return pu, dpu, pd, dpd, gc + pc, dgc + dpc

    def _at(self, name, V):
        if name not in self.t:
            return 0.0, 0.0
        ref = self.t[name][2]
        if name in ('pullup', 'power clamp'):
            i, s = self._one(name, ref - V)
            return i, -s
        i, s = self._one(name, V - ref)
        return i, s

    def solve(self, ku, kd, r, vfix, v0=None):
        """The die's voltage into a fixture r to vfix at Ku, Kd (steady)."""
        v = self.vcc / 2 if v0 is None else v0
        for _ in range(100):
            pu, dpu, pd, dpd, cl, dcl = self.parts(v)
            F = ku * pu + kd * pd + cl + (v - vfix) / r
            J = ku * dpu + kd * dpd + dcl + 1.0 / r
            step = F / J
            v -= float(np.clip(step, -0.5 * self.vcc, 0.5 * self.vcc))
            if abs(step) < 1e-9:
                break
        return v


def _switching(b, cv, edge, corner, c_comp):
    """(t, Ku, Kd) of an edge ('rising' | 'falling') from its V-T tables, or
    [Ramp]'s: the module's text."""
    k = CORNERS.index(corner)
    up = edge == 'rising'
    waves = [w for w in b.waves.get(edge, []) if w.get('t') is not None
             and w.get('r')]
    if waves:
        ts = np.unique(np.concatenate([w['t'] for w in waves]))
        ts = np.linspace(ts[0], ts[-1], max(len(ts), 200))
        rows = []
        for w in waves:
            v = np.interp(ts, w['t'], w['V'][:, k])
            dv = np.gradient(v, ts)
            vf = w['v'][k] if w['v'][k] is not None else w['v'][0]
            rows.append((v, dv, w['r'], vf or 0.0, w.get('c') or 0.0))
        Ku, Kd = np.zeros(len(ts)), np.zeros(len(ts))
        for i in range(len(ts)):
            A, rhs = [], []
            for v, dv, r, vf, cf in rows:
                pu, _, pd, _, cl, _ = cv.parts(v[i])
                A.append([pu, pd])
                rhs.append(-(v[i] - vf) / r - (c_comp + cf) * dv[i] - cl)
            A, rhs = np.array(A), np.array(rhs)
            sol = None
            if len(rows) >= 2:
                sol = np.linalg.lstsq(A, rhs, rcond=None)[0]
                if (np.linalg.cond(A) > 1e8
                        or not np.all(np.isfinite(sol))):
                    sol = None
            if sol is None:
                # one equation: Kd = 1 - Ku
                pu, pd = A[0]
                den = pu - pd
                ku = (rhs[0] - pd) / den if abs(den) > 1e-15 else (
                    1.0 if up else 0.0)
                sol = (ku, 1.0 - ku)
            Ku[i], Kd[i] = sol
        Ku, Kd = np.clip(Ku, -0.1, 1.1), np.clip(Kd, -0.1, 1.1)
        return ts - ts[0], Ku, Kd
    # [Ramp]: a linear 20 - 80 % edge into R_load (to ground rising, to the
    # supply falling)
    rv = b.ramp.get('r' if up else 'f')
    if not rv:
        raise IbisError(f"model {b.name}: no [{edge.title()} Waveform] and no "
                        "[Ramp]")
    dv, dt = rv[k] if rv[k] is not None else rv[0]
    rl = b.ramp.get('r_load') or 50.0
    vfix = 0.0 if up else cv.vcc
    lo = cv.solve(0.0, 1.0, rl, vfix)
    hi = cv.solve(1.0, 0.0, rl, vfix)
    # (the 20 - 80 % slope dv / dt: the whole swing in swing dt / dv)
    T = abs((hi - lo) * dt / dv) if dv else dt / 0.6
    ts = np.linspace(0.0, 1.2 * T, 200)
    frac = np.clip(ts / T, 0.0, 1.0)
    v = lo + (hi - lo) * (frac if up else 1 - frac)
    dvdt = np.gradient(v, ts)
    Ku = np.zeros(len(ts))
    for i in range(len(ts)):
        pu, _, pd, _, cl, _ = cv.parts(v[i])
        rhs = -(v[i] - vfix) / rl - c_comp * dvdt[i] - cl
        den = pu - pd
        Ku[i] = (rhs - pd) / den if abs(den) > 1e-15 else frac[i]
    Ku = np.clip(Ku, -0.1, 1.1)
    return ts, Ku, 1.0 - Ku


class _Die:
    """A buffer at its die behind its package, as a port's termination: the
    die's voltage solved at each step (Newton), the package's L and the C's
    by backward Euler."""

    def __init__(self, b, corner, package):
        self.cv = _Curves(b, corner)
        self.c_comp = b.value('c_comp', corner, 0.0) or 0.0
        r, l, c = package if package is not None else (0.0, 0.0, 0.0)
        self.r, self.l, self.c_pkg = r or 0.0, l or 0.0, c or 0.0
        self.vd = self.vp = self.il = 0.0

    def k(self, t):
        return 0.0, 0.0

    def _ibuf(self, v, t):
        ku, kd = self.k(t)
        pu, dpu, pd, dpd, cl, dcl = self.cv.parts(v)
        return ku * pu + kd * pd + cl, ku * dpu + kd * dpd + dcl

    def iv(self, v, t, dt):
        """The current into the channel's port at pin voltage v, dI/dv."""
        cd = self.c_comp / dt
        if self.r + self.l / dt < 1e-9:
            # (no package: the die at the pin)
            ib, gb = self._ibuf(v, t)
            cp = (self.c_comp + self.c_pkg) / dt
            self._vd_try = v
            return -ib - cp * (v - self.vp), -gb - cp
        a = 1.0 / (self.r + self.l / dt)
        vd = self.vd
        for _ in range(50):
            ib, gb = self._ibuf(vd, t)
            il = a * (vd - v + self.l / dt * self.il)
            h = ib + cd * (vd - self.vd) + il
            dh = gb + cd + a
            step = h / dh
            vd -= step
            if abs(step) < 1e-12:
                break
        ib, gb = self._ibuf(vd, t)
        il = a * (vd - v + self.l / dt * self.il)
        dvd = a / (gb + cd + a)
        self._vd_try, self._il_try = vd, il
        cp = self.c_pkg / dt
        return il - cp * (v - self.vp), a * (dvd - 1.0) - cp

    def accept(self, v, t, dt):
        self.iv(v, t, dt)
        self.vd = self._vd_try
        self.il = getattr(self, '_il_try', 0.0)
        self.vp = v


class Driver(_Die):
    """An IBIS output (or I/O) driving `bits` (0 / 1) at `ui` (s) from t0
    (s), the first bit held from t = 0 to it (the die, the package and the
    channel start at rest: hold it long enough to settle). corner: typ, min
    or max. A termination for core.macromodel.simulate."""

    def __init__(self, b, bits, ui, corner='typ', t0=0.0, package=None):
        if b.kind and not any(k in b.kind for k in ('output', 'i/o', '3-state',
                                                     'io')):
            raise IbisError(f"model {b.name} is {b.kind!r}: not a driver")
        super().__init__(b, corner, package if package is not None
                         else b.package)
        self.bits = np.asarray(bits, int)
        if b.polarity.startswith('inv'):
            self.bits = 1 - self.bits
        self.ui, self.t0 = float(ui), float(t0)
        self.edges = {e: _switching(b, self.cv, e, corner, self.c_comp)
                      for e in ('rising', 'falling')}
        # each transition's start in its table (the drive continuous)
        self._plan()
        # (at rest, as the channel is: the first bit held from t = 0 settles
        # them; core.timedomain starts the pattern after that)

    def settle_time(self):
        """The longer edge's table (s): a UI shorter than it truncates it."""
        return max(e[0][-1] for e in self.edges.values())

    def _plan(self):
        ev = []
        ku_now = 1.0 if self.bits[0] else 0.0
        for i in range(1, len(self.bits)):
            if self.bits[i] == self.bits[i - 1]:
                continue
            tt = self.t0 + i * self.ui
            name = 'rising' if self.bits[i] else 'falling'
            if ev:
                # where the last edge's table is now
                pt, pname, poff = ev[-1]
                ts, Ku, _ = self.edges[pname]
                ku_now = float(np.interp(tt - pt + poff, ts, Ku))
            ts, Ku, _ = self.edges[name]
            half = ts <= ts[-1] / 2 + 1e-30
            off = float(ts[half][np.argmin(np.abs(Ku[half] - ku_now))])
            ev.append((tt, name, off))
        self.events = ev
        self._times = np.array([e[0] for e in ev])

    def k(self, t):
        i = int(np.searchsorted(self._times, t, side='right')) - 1
        if i < 0:
            return (1.0, 0.0) if self.bits[0] else (0.0, 1.0)
        tt, name, off = self.events[i]
        ts, Ku, Kd = self.edges[name]
        x = t - tt + off
        return float(np.interp(x, ts, Ku)), float(np.interp(x, ts, Kd))


class Receiver(_Die):
    """An IBIS input (or an I/O's receiver: no drive) as a port's
    termination: its clamps and C_comp behind its package."""

    def __init__(self, b, corner='typ', package=None):
        super().__init__(b, corner, package if package is not None
                         else b.package)
