"""
The time loop: H and E updates on a backend, the extensions (PML slabs, Mur
boundaries, Debye poles, soft E excitations, lumped branches), probes, field
dumps, the energy end criterion (the field's and the lumped branches'), and
openEMS's outputs: its status lines on stdout, its probe files (port_ut_1 ...)
and its dump files in the run folder.

    Engine(op, backend='auto').run(path, nr_ts, end_crit)

Backends: vulkan (the GPU through Vulkan and slangpy: any Vulkan GPU) and core
(the compiled CPU core, gfdtd._core: OpenMP) run the compressed operator
(operator.compress: material codes and line arrays in place of the coefficient
arrays) through the same
update code (core/fdtd_core.h); numpy runs the dense coefficient arrays, slowly:
the reference.

A file ABORT in the run folder ends the run as with openEMS (kicad2ems's port
watch and Stop use it).
"""
import os, threading, time

import numpy as np
# the GPU's probe, shared with mom25d
from kicad2ems.emsdev import gpu as devices
# (gfdtd.engine.core_threads)
from kicad2ems.emsdev.cpu import core_threads  # noqa: F401

from .dumps import Dumps
from .operator import curl_I_part

STATUS_S = 1.0          # wall seconds between status lines
# GPU runs at once: up to this many cells in all (GFDTD_CONCURRENT_CELLS sets
# it)
CONCURRENT_CELLS = 1.2e6
# energy this many times its peak once the excitation has ended: unstable (a
# passive model's cannot pass its peak then; the measure swings with a
# resonance, E to H and back, 30 times on a small box: not against its least
# since)
GROWTH = 10.0
# dt^2 / (L C) of a lumped inductance and its edge's capacitance: at or above it
# the explicit branch update diverges (0.5 mm cells: 1.89 held, 2.52 grew
# without bound)
LUMPED_X = 2.0
# timesteps between energy / ABORT checks (one host sync each)
CHECK_STEPS = 100
# probe samples kept on the device before they are written out
CHUNK = 512


def _gpu_probes():
    """(name, probe) for each GPU backend: Vulkan's."""
    return list(PROBES.items())


def gpu_info():
    """(usable, one line): is there a GPU to run on, through the backend 'gpu'
    picks (gpu_backend)?"""
    why = []
    for _, probe in _gpu_probes():
        try:
            return True, probe().describe()
        # no slangpy, no driver, a broken install
        except devices.Unavailable as e:
            why.append(str(e))
    return False, "no GPU: " + "; ".join(why)


# the Slang kernels' features (emsdev.vulkan)
VULKAN_NEEDS = ('bda', 'int64', 'float64', 'atomic_add_f32')
PROBES = dict(vulkan=lambda: devices.vulkan_device(need=VULKAN_NEEDS))
# backend names (GFDTD_BACKEND's too)
ALIASES = dict(vk='vulkan')


def backend_info(name):
    """One line: what backend `name` (as resolve_backend gives it) runs on."""
    try:
        if name in PROBES:
            return PROBES[name]().describe()
    except devices.Unavailable as e:
        return f"no GPU: {e}"
    return core_info()[1] if name == 'core' else 'numpy (slow)'


def gpu_backend():
    """The GPU backend 'gpu' means here, or None: Vulkan (slangpy)."""
    for name, probe in _gpu_probes():
        try:
            probe()
            return name
        except devices.Unavailable:
            pass
    return None


def core_info():
    """(usable, one line): is the compiled CPU core built?"""
    try:
        # (built?)
        from . import _core  # noqa: F401
        n = os.environ.get('OMP_NUM_THREADS') or core_threads()
        return True, f"compiled CPU core, {n} threads"
    except ImportError as e:
        return (
            False,
            f"no compiled CPU core (python -m kicad2ems.gfdtd.core.build): {e}")


def vulkan_gpus():
    """[(index, name)] of the Vulkan GPUs runs may be shared out to
    (EMSDEV_VULKAN_DEVICE=index in each run's process): the discrete ones with
    the kernels' features (an integrated GPU's memory is the system's); [] where
    Vulkan is not there. Threads cannot share them out: two driving two GPUs
    from one process ran each at a fifth of its speed, the interpreter's lock
    between them (R-D9)."""
    try:
        from kicad2ems.emsdev import vulkan
        ads = vulkan.adapters()
    # (no Vulkan: no GPUs to share)
    except Exception:  # noqa: BLE001
        return []
    return [(a.index, a.name)
            for a in ads
            if a.kind == 'discrete' and (a.features is None or all(
                a.features.get(f) for f in VULKAN_NEEDS))]


def concurrent_runs(cells, runs):
    """How many runs of a model of `cells` to have on the GPU at once. Small
    models leave it idle between kernel launches, and two to four runs side by
    side fill it. That holds while their fields fit in its cache together:
    on an RX 7800 XT (64 MB Infinity Cache) 4 runs of 0.29 M cells gave 1.46
    times one run's throughput and 2 of 0.59 M cells 1.16, but 2 of 1.48 M cells
    0.64 (they thrash the cache). Hence up to CONCURRENT_CELLS in all."""
    limit = float(os.environ.get('GFDTD_CONCURRENT_CELLS') or CONCURRENT_CELLS)
    return max(1, min(int(runs), int(limit // max(cells, 1))))


def runs_that_fit(cells, runs, free):
    """How many of `runs` runs of a model of `cells` fit in `free` bytes of GPU
    memory at once (each excitation's fields are its own: gpu_bytes per run,
    its PML left out, within fits' 0.92), at least 1; free None: all of them."""
    if not free:
        return max(1, int(runs))
    per = 29.0 * cells + 64e6
    return max(1, min(int(runs), int(0.92 * free // per)))


def resolve_backend(name='auto'):
    """'vulkan', 'core' or 'numpy' for a backend name: auto (the GPU, else the
    core, else numpy), gpu or vulkan (vk), cpu (the core, else numpy), core,
    numpy. GFDTD_BACKEND overrides it."""
    name = devices.preference(name, 'GFDTD_BACKEND', ALIASES)
    if name in ('gpu', 'auto'):
        gpu = gpu_backend()
        if gpu or name == 'gpu':
            return gpu or 'vulkan'  # gpu without one: the Vulkan error says why
        name = 'cpu'
    if name == 'cpu':
        name = 'core' if core_info()[0] else 'numpy'
    if name not in ('vulkan', 'core', 'numpy'):
        raise ValueError(
            f"gfdtd: backend {name!r}: auto, gpu (vulkan), cpu (core, numpy)")
    return name


def _lumped_stable(op, idx, L, kind):
    """ValueError where a lumped inductance is too small for its edge and the
    time step (dt^2 / (L C_edge) at or above LUMPED_X: its explicit update grows
    without bound, which the run had shown only as a port not excited)."""
    if not len(idx):
        return
    # vi: dt / C of the edge
    x = (op.dt / L) * np.asarray(op.vi.reshape(-1)[idx], float)
    bad = x >= LUMPED_X
    if bad.any():
        k = int(np.argmax(x))
        need = L[k] * x[k] / LUMPED_X
        raise ValueError(
            f"gfdtd: {kind} lumped inductance of {L[k] * 1e12:.3g} pH on one "
            "edge is too small for its cell and the time step (dt^2 / (L C) = "
            f"{x[k]:.2f}, at least {LUMPED_X:g} diverges): {need * 1e12:.3g} "
            "pH or more on that edge, larger cells there, or openEMS"
        )


class NumpyBackend:
    """The dense reference: coefficient arrays vv, vi, iv, numpy slices."""
    name = 'numpy'
    dense = True

    def __init__(self, op, dtype=np.float32):
        self.op, self.dt = op, dtype
        self.xp = np
        self.V, self.I = (np.zeros((3,) + op.shape, dtype) for _ in range(2))
        self.vv, self.vi, self.iv = (np.ascontiguousarray(a, dtype)
                                     for a in (op.vv, op.vi, op.iv))
        self.ii = (np.ascontiguousarray(op.ii, dtype) if getattr(
            op, 'ii', None) is not None else None)
        self._sparse()

    def _sparse(self):
        """The sparse extensions, the probes, the PML slabs (their vi / iv
        slab-local) and the Debye entries."""
        xp, op, dt = self.xp, self.op, self.dt
        vi_at = lambda flat: xp.asarray(np.asarray(op.vi.reshape(-1)[flat], dt))
        self.exc_idx, self.exc_amp = (xp.asarray(op.exc[0]),
                                      xp.asarray(op.exc[1], dt))
        self.more = [
            dict(g, idx=xp.asarray(g['idx']), amp=xp.asarray(g['amp'], dt))
            for g in getattr(op, 'exc_more', [])
        ]
        if op.mur is not None:
            self.m0, self.m1, self.mk = (xp.asarray(op.mur[0]),
                                         xp.asarray(op.mur[1]),
                                         xp.asarray(op.mur[2], dt))
            self.o0, self.o1 = (xp.zeros(len(op.mur[0]), dt) for _ in range(2))
        if op.par_L:
            idx, L = zip(*op.par_L)
            idx = np.array(idx, np.int64)
            _lumped_stable(op, idx, np.array(L, float), 'a parallel')
            self.pl_idx, self.pl_k = (xp.asarray(idx),
                                      xp.asarray(op.dt / np.array(L), dt))
            self.pl_vi = vi_at(idx)
            self.pl_I = xp.zeros(len(idx), dt)
            self.pl_L = xp.asarray(np.array(L, float))
        if op.series:
            idx, R, L, Cs = zip(*op.series)
            idx = np.array(idx, np.int64)
            R, L = np.array(R, float), np.array(L, float)
            Cinv = np.array([0.0 if c is None else 1.0 / c for c in Cs])
            den = L / op.dt + R / 2
            if np.any(den <= 0):
                raise ValueError("gfdtd: a series lumped element needs R or L")
            if np.any((L <= 0) & (Cinv > 0)):  # (it diverged at any R)
                raise ValueError(
                    "gfdtd: a series R and C without L is not stable in its "
                    "update: give it L (a part's ESL), or make the R and the C "
                    "two elements")
            _lumped_stable(op, idx[L > 0], L[L > 0], 'a series')
            self.se_idx, self.se_vi = xp.asarray(idx), vi_at(idx)
            self.se_a, self.se_b, self.se_c = (xp.asarray(
                v,
                dt) for v in ((L / op.dt - R / 2) / den, 1 / den, Cinv / den))
            self.se_I, self.se_q = (xp.zeros(len(idx), dt) for _ in range(2))
            self.se_L, self.se_Cinv = xp.asarray(L), xp.asarray(Cinv)
            self.dtf = dt(op.dt)
        # the entries of the post kernel (the GPU backends'): excitation, lumped
        # branches, Mur
        self.n_ex, self.n_pl, self.n_se = (len(op.exc[0]), len(op.par_L),
                                           len(op.series))
        self.n_mu = len(op.mur[0]) if op.mur is not None else 0
        vt = [p for p in op.probes if p['field'] == 'V']
        it = [p for p in op.probes if p['field'] == 'I']
        self.order = vt + it  # buffer columns: voltage probes, then current

        def terms(ps, off):
            if not ps:
                return None
            idx = np.concatenate([p['idx'] for p in ps])
            w = np.concatenate([p['w'] for p in ps])
            pid = np.concatenate([
                np.full(len(p['idx']), off + n, np.int64)
                for n, p in enumerate(ps)
            ])
            return xp.asarray(idx), xp.asarray(w, dt), xp.asarray(pid)
        self.pv, self.pi = terms(vt, 0), terms(it, len(vt))
        self.np_ = len(self.order)
        self.buf = xp.zeros((CHUNK, max(self.np_, 1)), dt)
        self.pml = []
        for sl in op.pml:
            a, n = sl['axis'], sl['n']
            t1, t2 = (a + 1) % 3, (a + 2) % 3
            local = lambda F, s0: np.ascontiguousarray(
                np.take(F, range(s0, s0 + n), axis=a), dt).ravel()
            m = n * int(np.prod(op.shape)) // op.shape[a]
            self.pml.append(
                dict(
                    sl, psiE=xp.zeros(2 * m, dt), psiH=xp.zeros(2 * m, dt), m=m,
                    kE1=xp.asarray(local(op.vi[t1], sl['e0'])),
                    kE2=xp.asarray(local(op.vi[t2], sl['e0'])),
                    kH1=xp.asarray(local(op.iv[t1], sl['h0'])),
                    kH2=xp.asarray(local(op.iv[t2], sl['h0'])), **{
                        k: xp.asarray(sl[k], dt)
                        for k in ('bE', 'cE', 'bH', 'cH')
                    }))
        fe = getattr(op, 'ferrite', None)
        self.n_fc = fe.nc if fe is not None else 0
        self.n_fe = fe.ne if fe is not None else 0
        # the ferrite's components: 2 (along +-z, the fast path) or 3; faces:
        # each face's own difference too (operator._ferrite)
        self.fc_na = getattr(fe, 'na', 2) if fe is not None else 2
        self.fc_faces = bool(getattr(fe, 'faces', False))
        if fe is not None:
            na, fc = self.fc_na, self.fc_faces
            f32 = lambda a: np.ascontiguousarray(a, np.float32).ravel()
            self.fc_e = np.ascontiguousarray(fe.e, np.int32).ravel()
            self.fc_k = f32(fe.k)
            self.fc_s = np.zeros((5 if fc else 2) * na * fe.nc, np.float32)
            self.fc_d = np.zeros((2 if fc else 1) * na * (fe.nc + 1),
                                 np.float32)
            self.fe_i = np.asarray(fe.idx, np.int64)
            self.fe_l, self.fe_li = f32(fe.lh), f32(1.0 / fe.lh)
            self.fe_c = np.ascontiguousarray(fe.cells, np.int32).ravel()
            self.fe_w = f32(fe.w)
            self.fe_m = np.zeros(fe.ne, np.float32)
        if self.dense and op.debye is not None:
            idx, ka, kb, sg = op.debye
            self.db_idx = xp.asarray(idx)
            self.db_ka, self.db_kb, self.db_h, self.db_sg = (xp.asarray(
                v, dt) for v in (ka, kb, 0.5 * (1 + ka), sg))
            self.db_vi = vi_at(idx)
            self.db_i = xp.zeros(len(idx), dt)

    # ---- the updates --------------------------------------------------------
    def step(self, sig=None, n=None):
        """One timestep: H, its PML, the ferrite, the H excitations, E (with the Debye
        poles), its PML, the excitation (sig: the signal's value, None past its
        end) and the delayed ones (n: the step), the lumped branches and Mur."""
        self.update_H()
        self.pml_h()
        self.ferrite()
        if self.more and n is not None:
            self.excite_more('I', n)
        self.mur_pre()
        self.update_E()
        self.pml_e()
        if sig is not None:
            self.excite(sig)
        if self.more and n is not None:
            self.excite_more('V', n)
        self.lumped()
        self.mur_post()

    def excite_more(self, field, n):
        """The excitation groups on field 'V' or 'I' at step n: each its delay
        late, on the H signal (half a step later) for 'I'; a hard group sets its
        edges, a soft one adds to them."""
        sig = self.op.signal_h if field == 'I' else self.op.signal
        F = self.I if field == 'I' else self.V
        for g in self.more:
            if g['field'] != field:
                continue
            k = n - g['delay']
            val = float(sig[k]) if 0 <= k < len(sig) else 0.0
            if g['hard']:
                self.assign(F, g['idx'], g['amp'], val)
            elif val:
                self.add(F, g['idx'], g['amp'], val)

    def add(self, F, idx, amp, val):
        np.add.at(F.reshape(-1), idx, amp * self.dt(val))

    def assign(self, F, idx, amp, val):
        F.reshape(-1)[idx] = amp * self.dt(val)

    def update_H(self):
        (Vx, Vy, Vz), (Ix, Iy, Iz), (ax, ay, az) = self.V, self.I, self.iv
        # magnetic loss: I' = ii I - iv curl V
        if getattr(self, 'ii', None) is not None:
            self.I *= self.ii
        Ix[:, :-1, :-1] -= (ax[:, :-1, :-1] *
                            (Vz[:, 1:, :-1] - Vz[:, :-1, :-1] - Vy[:, :-1, 1:]
                             + Vy[:, :-1, :-1]))
        Iy[:-1, :, :-1] -= (ay[:-1, :, :-1] *
                            (Vx[:-1, :, 1:] - Vx[:-1, :, :-1] - Vz[1:, :, :-1]
                             + Vz[:-1, :, :-1]))
        Iz[:-1, :-1, :] -= (az[:-1, :-1, :] *
                            (Vy[1:, :-1, :] - Vy[:-1, :-1, :] - Vx[:-1, 1:, :]
                             + Vx[:-1, :-1, :]))

    def ferrite(self):
        """The ferrite's magnetisation (operator._ferrite): each cell's from
        the b on its edges, then the edges' h less the change."""
        if not self.n_fc:
            return
        I = self.I.reshape(-1)
        b = I[self.fe_i] * self.fe_li + self.fe_m
        na, fc = self.fc_na, self.fc_faces
        e = self.fc_e.reshape(-1, 2 * na)
        b0, b1 = b[e[:, 0::2]], b[e[:, 1::2]]       # the lower, upper faces'
        B = 0.5 * (b0 + b1)                         # (nc, na) at the centres
        st = self.fc_s.reshape(-1, (5 if fc else 2) * na)
        d = self.fc_d.reshape((2 if fc else 1) * na, -1)
        m, S = st[:, :na].copy(), B + st[:, na:2 * na]
        if na == 2:
            k = self.fc_k.reshape(-1, 4)
            nx = k[:, 0] * m[:, 0] - k[:, 1] * m[:, 1] + k[:, 2] * S[:, 0] \
                - k[:, 3] * S[:, 1]
            ny = k[:, 0] * m[:, 1] + k[:, 1] * m[:, 0] + k[:, 2] * S[:, 1] \
                + k[:, 3] * S[:, 0]
            new = np.stack([nx, ny], axis=1)
            dm = new - m
            fac, qs = 1.0, 0.0
        else:
            k = self.fc_k.reshape(-1, 8)
            u = k[:, 4:7]
            us = (u * S).sum(axis=1, keepdims=True)
            new = (k[:, 0:1] * m + k[:, 1:2] * np.cross(u, m)
                   + k[:, 2:3] * (S - u * us) + k[:, 3:4] * np.cross(u, S))
            qs = k[:, 7:8]
            dm = new - m + qs * (B - st[:, na:2 * na])
            fac = 1 - u * u
        if fc:
            # each component's faces' difference D = (b1 - b0) / 2 drives a
            # precession of its own (the cell's coefficients) from (D, 0):
            # its first part Xd D, times 1 - u_a^2 (Xd P's a, a), and the
            # instant qs D: the lower face takes the mean's change less it,
            # the upper one plus it
            D = 0.5 * (b1 - b0)
            mD, qD = st[:, 2 * na:3 * na].copy(), st[:, 3 * na:4 * na].copy()
            SD = D + st[:, 4 * na:5 * na]
            k0, k1, k2, k3 = (k[:, j:j + 1] for j in range(4))
            mDn = k0 * mD - k1 * qD + k2 * SD
            st[:, 3 * na:4 * na] = k0 * qD + k1 * mD + k3 * SD
            cD = fac * (mDn - mD) + qs * (D - st[:, 4 * na:5 * na])
            st[:, 2 * na:3 * na], st[:, 4 * na:5 * na] = mDn, D
            d[0::2, :-1] = (dm - cD).T
            d[1::2, :-1] = (dm + cD).T
        else:
            d[:, :-1] = dm.T
        st[:, :na], st[:, na:2 * na] = new, B
        c, w = self.fe_c.reshape(-1, 2), self.fe_w.reshape(-1, 2)
        delta = w[:, 0] * self.fc_d[c[:, 0]] + w[:, 1] * self.fc_d[c[:, 1]]
        self.fe_m += delta
        I[self.fe_i] -= delta * self.fe_l

    def update_E(self):
        """The E update with the poles folded in: V' = vv V + vi (c - sum h i),
        i' = ka i + kb (V' - sg V) (sg 1: Debye, -1: a conducting sheet's R-L
        branch)."""
        v = self.V.reshape(-1)
        if self.op.debye is not None:
            old = v[self.db_idx]
        for a in range(3):
            self.V[a] *= self.vv[a]
            self.V[a] += self.vi[a] * curl_I_part(self.I, a)
        if self.op.debye is not None:
            np.subtract.at(v, self.db_idx, self.db_vi * self.db_h * self.db_i)
            self.db_i[:] = (self.db_ka * self.db_i + self.db_kb *
                            (v[self.db_idx] - self.db_sg * old))

    def _pml(self, field):
        """Each slab's psi update and terms (numpy): the slab's axis moved
        first."""
        for sl in self.pml:
            a, n = sl['axis'], sl['n']
            t1, t2 = (a + 1) % 3, (a + 2) % 3
            s0 = sl[field.lower() + '0']
            b_, c_ = (sl['b' + field].reshape(-1, 1, 1),
                      sl['c' + field].reshape(-1, 1, 1))
            mv = lambda F: np.moveaxis(F, a, 0)
            psi = sl['psi' + field].reshape((2, n) + tuple(
                m for b, m in enumerate(self.op.shape) if b != a))
            if field == 'E':
                V1, V2, I1, I2, k1, k2 = (mv(self.V[t1]), mv(self.V[t2]),
                                          mv(self.I[t1]), mv(self.I[t2]),
                                          mv(self.vi[t1]), mv(self.vi[t2]))

                def diff(F):  # backward, I[-1] = 0
                    d = F[s0:s0 + n].copy()
                    if s0 > 0:
                        d -= F[s0 - 1:s0 + n - 1]
                    else:
                        d[1:] -= F[:n - 1]
                    return d
                psi[0] = b_ * psi[0] + c_ * diff(I2)
                V1[s0:s0 + n] -= k1[s0:s0 + n] * psi[0]
                psi[1] = b_ * psi[1] + c_ * diff(I1)
                V2[s0:s0 + n] += k2[s0:s0 + n] * psi[1]
            else:
                I1, I2, V1, V2, k1, k2 = (mv(self.I[t1]), mv(self.I[t2]),
                                          mv(self.V[t1]), mv(self.V[t2]),
                                          mv(self.iv[t1]), mv(self.iv[t2]))
                N = V1.shape[0]

                def diff(F):  # forward, V[N] = 0
                    d = -F[s0:s0 + n]
                    if s0 + n < N:
                        d = d + F[s0 + 1:s0 + n + 1]
                    else:
                        d[:-1] += F[s0 + 1:s0 + n]
                    return d
                psi[0] = b_ * psi[0] + c_ * diff(V2)
                I1[s0:s0 + n] += k1[s0:s0 + n] * psi[0]
                psi[1] = b_ * psi[1] + c_ * diff(V1)
                I2[s0:s0 + n] -= k2[s0:s0 + n] * psi[1]

    def pml_h(self):
        self._pml('H')

    def pml_e(self):
        self._pml('E')

    def mur_pre(self):
        if self.op.mur is not None:
            v = self.V.reshape(-1)
            self.o0[:] = v[self.m0]
            self.o1[:] = v[self.m1]

    def mur_post(self):
        if self.op.mur is not None:
            v = self.V.reshape(-1)
            v[self.m0] = self.o1 + self.mk * (v[self.m1] - self.o0)

    def excite(self, s):
        if len(self.exc_idx):
            self.add(self.V, self.exc_idx, self.exc_amp, s)

    def lumped(self):
        v = self.V.reshape(-1)
        if self.op.par_L:
            v[self.pl_idx] -= self.pl_vi * self.pl_I
            self.pl_I += self.pl_k * v[self.pl_idx]
        if self.op.series:
            v[self.se_idx] -= self.se_vi * self.se_I
            self.se_q += self.dtf * self.se_I
            self.se_I[:] = (self.se_a * self.se_I + self.se_b * v[self.se_idx]
                            - self.se_c * self.se_q)

    def probe(self, slot):
        row = self.buf[slot]
        row[:] = 0
        for t, F in ((self.pv, self.V), (self.pi, self.I)):
            if t is not None:
                idx, w, pid = t
                np.add.at(row, pid, w * F.reshape(-1)[idx])

    def energy(self):
        """The energy (J) the end criterion watches: the field's and the lumped
        branches' (one host sync)."""
        return float(self.field_energy() + self.lumped_energy())

    def field_energy(self):
        with np.errstate(divide='ignore', invalid='ignore'):
            e = np.where(self.vi > 0, self.V * self.V / self.vi,
                         0).sum(dtype=np.float64)
            e += np.where(self.iv > 0, self.I * self.I / self.iv,
                          0).sum(dtype=np.float64)
        return e * self.op.dt / 2

    def lumped_energy(self):
        """The energy in the lumped branches (J, a scalar on the backend's
        device): L I^2 / 2 in the inductors, q^2 / 2C in the series capacitors.
        Not in the field: a lumped LC filter rings in its branches."""
        e = 0.0
        if self.op.par_L:
            i = self.pl_I.astype(np.float64)
            e = e + self.pl_L @ (i * i)
        if self.op.series:
            i, q = self.se_I.astype(np.float64), self.se_q.astype(np.float64)
            e = e + self.se_L @ (i * i) + self.se_Cinv @ (q * q)
        return e / 2

    def fetch(self, n):
        return np.array(self.buf[:n], float)

    def sync(self):
        pass

    def to_host(self, a):
        """A backend array as numpy."""
        return np.asarray(a)

    def fields(self):
        """(V, I) as arrays of xp (the dumps without a kernel compute on
        them)."""
        return self.V, self.I

    def zeros(self, shape, dtype=np.float32):
        return self.xp.zeros(shape, dtype)

    def up(self, a, dtype=None):
        return self.xp.asarray(a, dtype)


class _Compressed:
    """Setup shared by the backends on the compressed operator: its tables on
    the device (xp)."""
    dense = False

    def _tables(self):
        from .operator import compress
        xp, op = self.xp, self.op
        self.cpr = c = compress(op)
        f32 = lambda a: xp.asarray(np.ascontiguousarray(a, np.float32))
        self.code = xp.asarray(c.code)
        self.tvv, self.ts, self.G = (f32(c.vv.ravel()), f32(c.s.ravel()),
                                     f32(c.G))
        self.tpole = xp.asarray(np.ascontiguousarray(c.pole.ravel(), np.int32))
        self.P = int(c.P)
        self.tkb = f32(c.kbt.reshape(-1) if c.kbt.size else np.zeros(1))
        self.tka, self.th = (f32(c.ka if c.P else np.zeros(1)),
                             f32(c.h if c.P else np.zeros(1)))
        self.slot = xp.asarray(c.slot)
        self.pol = xp.zeros(max(1, c.nslot * 3 * c.P), np.float32)
        self.ke, self.kh, self.dtf32 = (np.float32(c.ke), np.float32(c.kh),
                                        np.float32(op.dt))
        self.idims = tuple(int(v) for v in op.shape)

    def _choose_mode(self):
        """The GPU backends' step: 'fused' (three launches; the post kernel runs
        its excitation, lumped and Mur entries at once, so it needs their edges
        disjoint), 'split' (the PML apart) or 'separate' (the kernels one by
        one). GFDTD_STEP sets it (for checks: one way only), else _pick_mode
        where there is PML."""
        self.fused = _post_disjoint(self.op)
        self.mode = 'fused' if self.fused else 'separate'
        if os.environ.get('GFDTD_STEP') in ('fused', 'split', 'separate'):
            self.mode = os.environ['GFDTD_STEP'] if self.fused else 'separate'
        elif self.fused and self.pml:
            self.mode = self._pick_mode()

    def _pick_mode(self, seconds=0.01, rounds=3):
        """PML in the H / E kernels ('fused') or as its own launch per field
        ('split'): whichever runs faster here (the best of a few rounds of
        `seconds` each, the modes alternating). In-kernel PML won on small
        models (fewer launches) and on large ones (no second pass over the
        slabs), and lost at ~1 M cells, where the fields sit in the GPU's cache
        and the bigger kernels' occupancy counts more. The trial steps change
        nothing: the fields are still zero and nothing excites them."""
        best = {}
        for _ in range(rounds):
            for mode in ('fused', 'split'):
                self.mode = mode
                self.step(None)
                self.sync()
                n, t0 = 0, time.perf_counter()
                while True:
                    for _ in range(10):
                        self.step(None)
                    n += 10
                    self.sync()
                    dt = time.perf_counter() - t0
                    if dt >= seconds:
                        break
                best[mode] = min(best.get(mode, dt / n), dt / n)
        return min(best, key=best.get)


def _pml_layout(pml, shape, key):
    """The one-launch PML kernels' slab table, a row per slab (start, m, axis,
    s0, n, offset of b / c: kernels_vk.SLAB), and the PML-free interior (lines
    [lo, hi) per axis), for the E slabs (key 'e0') or the H ones ('h0')."""
    desc, st, bo = [], 0, 0
    lo, hi = [0, 0, 0], list(shape)
    for sl in pml:
        a, s0 = sl['axis'], sl[key]
        desc.append([st, sl['m'], a, s0, sl['n'], bo])
        st, bo = st + sl['m'], bo + sl['n']
        if sl['hi']:
            hi[a] = min(hi[a], s0)
        else:
            lo[a] = max(lo[a], s0 + sl['n'])
    return desc, lo + hi


class CoreBackend(_Compressed, NumpyBackend):
    """The compiled CPU core (gfdtd._core, OpenMP) on the compressed operator;
    the sparse extensions as numpy."""
    name = 'core'

    def __init__(self, op, dtype=np.float32):
        from . import _core
        if dtype != np.float32:
            raise ValueError("gfdtd: the CPU core is float32")
        self.core, self.op, self.dt, self.xp = _core, op, dtype, np
        if not os.environ.get('OMP_NUM_THREADS'):
            _core.set_threads(core_threads())
        self.V, self.I = (np.zeros((3,) + op.shape, dtype) for _ in range(2))
        self._tables()
        self._sparse()

    def update_H(self):
        self.core.update_h(self.V, self.I, self.G, *self.idims, self.kh)

    def update_E(self):
        self.core.update_e(self.V, self.I, self.code, self.tvv, self.ts,
                           self.tpole, self.tkb, self.tka, self.th, self.P,
                           self.slot, self.pol, self.G, *self.idims, self.ke,
                           self.dtf32)

    def _pml(self, f):
        update = self.core.pml_e if f == 'E' else self.core.pml_h
        for sl in self.pml:
            update(self.V, self.I, sl['psi' + f], sl['b' + f], sl['c' + f],
                   sl[f'k{f}1'], sl[f'k{f}2'], *self.idims, sl['axis'],
                   sl[f.lower() + '0'], sl['n'])

    def ferrite(self):
        if not self.n_fc:
            return
        # a core built before the faces' scheme (no ferrite3: its ferrite
        # reads the old state, 4 floats a cell) runs it as NumPy does
        if not hasattr(self.core, 'ferrite3'):
            return NumpyBackend.ferrite(self)
        f = getattr(self.core, 'ferrite' if self.fc_na == 2 else 'ferrite3')
        f(self.I, self.fc_e, self.fc_k, self.fc_s, self.fc_d, self.fe_i,
          self.fe_l, self.fe_li, self.fe_c, self.fe_w, self.fe_m)

    def mur_pre(self):
        if self.op.mur is not None:
            self.core.mur_pre(self.V, self.m0, self.m1, self.o0, self.o1)

    def mur_post(self):
        if self.op.mur is not None:
            self.core.mur_post(self.V, self.m0, self.m1, self.mk, self.o0,
                               self.o1)

    def field_energy(self):
        return (self.core.energy(self.V, self.I, self.code, self.ts, self.G, *
                                 self.idims, self.ke, self.kh) * self.op.dt / 2)


class _Dev:
    """A device array of the Vulkan backend: its buffer, address and length
    (len() as a numpy array's)."""
    __slots__ = ('buf', 'addr', 'n', 'dtype', 'shape')

    def __init__(self, buf, n, dtype, shape=None):
        self.buf, self.addr, self.n, self.dtype = (buf, buf.device_address,
                                                   int(n), np.dtype(dtype))
        self.shape = shape if shape is not None else (self.n,)

    def __len__(self):
        return self.n


class VulkanBackend(_Compressed, NumpyBackend):
    """The GPU through Vulkan (slangpy: Slang kernels, kernels_vk.py) on the
    compressed operator: any Vulkan GPU. Its steps (fused / split / separate:
    _choose_mode) are recorded into command buffers that are submitted every
    SUBMIT launches and at each host read, so the host records while the GPU
    runs."""
    name = 'vulkan'
    # threads a workgroup: 64 ran 1-4 % faster than 128 / 256 on an RX 7800 XT
    BLOCK = 64
    SUBMIT = 96                 # launches per command buffer, at most
    # the first after a sync: the GPU starts while the host records the rest (it
    # is idle after each sync, the energy check's every CHECK_STEPS)
    FIRST = 6
    NBLK = 1024                 # the energy kernel's workgroups
    # the kernels' index type (kernels_vk.source idx: int)
    IDX = np.int32

    def __init__(self, op, dtype=np.float32):
        from kicad2ems.emsdev import vulkan
        from . import kernels_vk
        if dtype != np.float32:
            raise ValueError("gfdtd: the GPU kernels are float32")
        try:
            info = vulkan.device(need=VULKAN_NEEDS)
            self.dev = vulkan.slang_device()
        except vulkan.Unavailable as e:
            raise RuntimeError(f"gfdtd: no Vulkan GPU: {e}")
        try:
            free = info.free()
        # (not known: no check)
        except Exception:  # noqa: BLE001
            free = None
        fits(op, free, info.name)
        import slangpy as spy
        self.spy, self.op, self.dt, self.xp = spy, op, dtype, np
        nx, ny, nz = op.shape
        self.n = nx * ny * nz
        self._keep, self.enc, self.launches, self.batch = ([], None, 0,
                                                           self.FIRST)
        self.usage = (spy.BufferUsage.shader_resource
                      | spy.BufferUsage.unordered_access
                      | spy.BufferUsage.copy_source
                      | spy.BufferUsage.copy_destination)
        self.max_groups = int(
            self.dev.info.limits.max_compute_dispatch_thread_groups[0])
        self._tables()  # host arrays (xp = numpy), uploaded below
        self._sparse()
        ctype = {
            np.dtype(np.uint8): 'uint8_t',
            np.dtype(np.uint16): 'uint16_t'
        }.get(np.dtype(self.code.dtype), 'uint32_t')
        self.k = _vk_kernels(self.dev, ctype, self.BLOCK, 'int')
        self.V, self.I = (self.zeros((3,) + op.shape) for _ in range(2))
        # Model's fields: the arrays' addresses (these uploaded under their own
        # names), then the values
        dtypes = dict(
            pl_L=np.float64, se_L=np.float64, se_Cinv=np.float64,
            **dict.fromkeys(('pl_idx', 'se_idx', 'm0', 'm1', 'fe_i'), self.IDX))
        names = [
            'G', 'code', 'tvv', 'ts', 'tpole', 'tkb', 'tka', 'th', 'slot', 'pol'
        ]
        names += ['pl_vi', 'pl_idx', 'pl_k', 'pl_I', 'pl_L'] if op.par_L else []
        names += ([
            'se_vi', 'se_idx', 'se_a', 'se_b', 'se_c', 'se_I', 'se_q', 'se_L',
            'se_Cinv'
        ] if op.series else [])
        names += ['m0', 'm1', 'mk', 'o0', 'o1'] if op.mur is not None else []
        names += (['fc_e', 'fc_k', 'fc_s', 'fc_d', 'fe_i', 'fe_l', 'fe_li',
                   'fe_c', 'fe_w', 'fe_m'] if self.n_fc else [])
        f = dict(V=self.V, I=self.I,
                 **{n: self.up(getattr(self, n), dtypes.get(n))
                    for n in names})
        self._pml_all(f)
        self.exc_idx, self.exc_amp = (self.up(self.exc_idx,
                                              self.IDX), self.up(self.exc_amp))
        f.update(ex_idx=self.exc_idx, ex_amp=self.exc_amp)
        for g in self.more:
            g['idx'], g['amp'] = self.up(g['idx'], self.IDX), self.up(g['amp'])
        for key, t in (('pv', self.pv), ('pi', self.pi)):
            if t is not None:
                f.update({
                    key + '_idx': self.up(t[0],
                                          self.IDX), key + '_w': self.up(t[1]),
                    key + '_pid': self.up(t[2], self.IDX)
                })
        # the energy's partial sums, written there
        self.hpart = vulkan.HostBuffer(self.dev, 8 * (self.NBLK + 1))
        self._hosts = [self.hpart]
        self.buf = f['buf'] = self.zeros(self.buf.shape)
        vals = dict(nx=nx, ny=ny, nz=nz, P=self.P, ns=len(self.pml),
                    n_ex=self.n_ex, n_pl=self.n_pl, n_se=self.n_se,
                    n_mu=self.n_mu,
                    n_pv=(len(self.pv[0]) if self.pv is not None else 0),
                    n_pi=(len(self.pi[0]) if self.pi is not None else 0),
                    np_=self.buf.shape[1], pml_M=self.pml_M, n_fc=self.n_fc,
                    n_fe=self.n_fe, pad=0,
                    kh=float(self.kh), ke=float(self.ke), dt=float(self.dtf32),
                    dtf=float(getattr(self, 'dtf', 0.0)))
        rec = np.zeros(1, [(n, {'p': '<u8', 'i': '<i4', 'f': '<f4'}[k])
                           for n, k in kernels_vk.MODEL])
        # an array the model has none of (no lumped parts, no Mur): a small
        # zeroed buffer rather than a null address, as loops over none of them
        # never read it - lavapipe (Mesa's CPU Vulkan) read through the null
        # pointer anyway, in the energy kernel's empty lumped loops, and
        # crashed (R-C7.2)
        self._none = self.zeros(16)
        for name, kind in kernels_vk.MODEL:
            rec[name] = (f[name].addr if kind == 'p' and name in f else
                         (self._none.addr if kind == 'p' else vals[name]))
        rec['part'] = self.hpart.address
        self.model = self.up(rec.view(np.uint8))
        self._choose_mode()

    # ---- device memory ------------------------------------------------------
    def _buffer(self, nbytes, data=None):
        # whole 16 bytes, the data padded to them
        size = max(-(-int(nbytes) // 16) * 16, 16)
        if data is not None and data.nbytes != size:
            pad = np.zeros(size, np.uint8)
            pad[:data.nbytes] = np.ascontiguousarray(data).view(
                np.uint8).ravel()
            data = pad
        try:
            b = self.dev.create_buffer(size=size, usage=self.usage)
        # past the driver's largest buffer, out of memory
        except Exception as e:
            raise MemoryError(f"gfdtd: a {nbytes / 1e9:.2f} GB GPU buffer: {e}")
        if data is not None:
            b.copy_from_numpy(data)
        self._keep.append(b)
        return b

    def up(self, a, dtype=None):
        """A host array on the device (a copy)."""
        a = np.ascontiguousarray(
            np.asarray(a) if dtype is None else np.asarray(a, dtype))
        if a.size == 0:
            a = np.zeros(4, a.dtype)
            return _Dev(self._buffer(16, a), 0, a.dtype)
        return _Dev(self._buffer(a.nbytes, a), a.size, a.dtype, a.shape)

    def zeros(self, shape, dtype=np.float32):
        shape = (shape,) if np.isscalar(shape) else tuple(shape)
        n = int(np.prod(shape))
        d = _Dev(self._buffer(n * np.dtype(dtype).itemsize), n, dtype, shape)
        self._clear(d)
        return d

    def _clear(self, d):
        self._encoder().clear_buffer(d.buf)

    def to_host(self, d):
        """A device array as numpy (a host read: everything recorded so far runs
        first). Through mapped host memory, filled by a kernel (copy_words)."""
        if not isinstance(d, _Dev):
            return np.asarray(d)
        from kicad2ems.emsdev.vulkan import HostBuffer
        nbytes = d.n * d.dtype.itemsize
        h = getattr(self, '_stage', None)
        # the staging buffer, grown to the largest read
        if h is None or h.nbytes < nbytes:
            if h is not None:
                h.free()
            h = self._stage = HostBuffer(self.dev, max(nbytes, 1 << 16))
            self._hosts.append(h)
        self._launch('copy_words', -(-nbytes // 4), src_w=d.addr,
                     dst_w=h.address, nw_=-(-nbytes // 4))
        self.sync()
        return h.array[:nbytes].view(d.dtype).reshape(d.shape).copy()

    def fields(self):
        """(V, I) as numpy, for what has no kernel (dumps of Jc)."""
        return self.to_host(self.V), self.to_host(self.I)

    # ---- launches -----------------------------------------------------------
    def _encoder(self):
        if self.enc is None:
            self.enc = self.dev.create_command_encoder()
        return self.enc

    def _launch(self, name, m, **vars):
        """Kernel `name` over m items (a grid-stride loop), recorded after a
        barrier on what came before."""
        m = int(m)
        if m <= 0:
            return
        groups = min((m + self.BLOCK - 1) // self.BLOCK, self.max_groups)
        enc = self._encoder()
        # the pass and cursor directly: half the host time of
        # ComputeKernel.dispatch(vars=...)
        p = enc.begin_compute_pass()
        cur = self.spy.ShaderCursor(p.bind_pipeline(self.k[name]))
        cur['M'] = self.model.addr
        cur['stride'] = groups * self.BLOCK
        for key, v in vars.items():
            cur[key] = v
        p.dispatch_compute([groups, 1, 1])
        p.end()
        # the kernels reach memory by address: no resource tracking, so a
        # barrier each
        enc.global_barrier()
        self.launches += 1
        if self.launches >= self.batch:
            self._submit()
            self.batch = min(2 * self.batch, self.SUBMIT)

    def _submit(self):
        if self.enc is not None:
            self.dev.submit_command_buffer(self.enc.finish())
            self.enc, self.launches = None, 0

    def sync(self):
        self._submit()
        self.dev.wait_for_idle()
        self.batch = self.FIRST

    # ---- the step -----------------------------------------------------------
    def step(self, sig=None, n=None):
        """One timestep in three launches: H with its PML and the Mur copies, E
        with the Debye poles and its PML, then the excitation, lumped branches
        and Mur update together (or the separate kernels: _choose_mode). The H
        excitations and the delayed ones (n: the step) run between them."""
        if self.mode == 'separate':
            return NumpyBackend.step(self, sig, n)
        split = self.mode == 'split'
        ns = 0 if split else len(self.pml)
        self._launch('step_h', self.n + self.n_mu, ns_=ns)
        if split:
            self._pml('H')
        self.ferrite()
        if self.more and n is not None:
            self.excite_more('I', n)
        self._launch('step_e', self.n, ns_=ns)
        if split:
            self._pml('E')
        if self.more and n is not None:
            self.excite_more('V', n)
        n_ex = self.n_ex if sig is not None else 0
        self._launch('step_post', n_ex + self.n_pl + self.n_se + self.n_mu,
                     n_ex_=n_ex, s_=float(sig or 0.0))

    def _pml_all(self, f):
        """The slabs concatenated for the one-launch PML kernels: per field psi,
        b, c, k1, k2, the slab table and the PML-free interior, into Model's
        fields."""
        self.pml_M = sum(sl['m'] for sl in self.pml)
        if not self.pml:
            return
        cat = lambda k: np.concatenate([sl[k] for sl in self.pml])
        for F in 'EH':
            desc, box = _pml_layout(self.pml, self.op.shape, F.lower() + '0')
            f.update({
                'psi' + F: self.zeros(2 * self.pml_M),
                'b' + F: self.up(cat('b' + F)), 'c' + F: self.up(cat('c' + F)),
                'k1' + F: self.up(cat(f'k{F}1')),
                'k2' + F: self.up(cat(f'k{F}2')),
                'desc' + F: self.up(np.array(desc, self.IDX).ravel(), self.IDX),
                'box' + F: self.up(np.array(box, np.int32))
            })

    def _pml(self, field):
        self._launch('pml_e' if field == 'E' else 'pml_h', self.pml_M)

    def update_H(self):
        self._launch('update_h', self.n)

    def ferrite(self):
        self._launch('ferrite_m' if self.fc_na == 2 else 'ferrite_m3',
                     self.n_fc)
        self._launch('ferrite_h', self.n_fe)

    def update_E(self):
        self._launch('update_e', self.n)

    def mur_pre(self):
        self._launch('mur_pre', self.n_mu)

    def mur_post(self):
        self._launch('mur_post', self.n_mu)

    def add(self, F, idx, amp, val):
        self._launch('excite', len(idx), F_=F.addr, idx_=idx.addr,
                     amp_=amp.addr, s_=float(val), m_=len(idx))

    def assign(self, F, idx, amp, val):
        self._launch('assign', len(idx), F_=F.addr, idx_=idx.addr,
                     amp_=amp.addr, s_=float(val), m_=len(idx))

    def lumped(self):
        self._launch('par_l', self.n_pl)
        self._launch('series_rlc', self.n_se)

    def probe(self, slot):
        """One sample into buffer row slot (rows are zeroed after each
        fetch)."""
        self._launch(
            'probe',
            sum(len(t[0]) for t in (self.pv, self.pi) if t is not None),
            slot_=int(slot))

    def energy(self):
        """The field's and the lumped branches' energy (one host read)."""
        self._launch('energy', self.NBLK * self.BLOCK, nblk_=self.NBLK)
        self.sync()
        p = self.hpart.view(np.float64, self.NBLK + 1)
        return float(p[:self.NBLK].sum() * (self.op.dt / 2) + p[self.NBLK] / 2)

    def fetch(self, n):
        rows = self.to_host(self.buf)[:n].astype(float)
        self._clear(self.buf)
        return rows

    # ---- dumps (dumps.Region on the GPU) ------------------------------------
    def dump_kernel(self, region, out, nf, t):
        a = region.args
        self._launch('dump',
                     int(np.prod(region.shape)) * 3, field_=int(a[0]),
                     cell_=int(a[1]), lx_=int(a[2]), ly_=int(a[3]),
                     lz_=int(a[4]), sx_=int(a[5]), sy_=int(a[6]), sz_=int(a[7]),
                     nf_=int(nf), omega_=region.omega.addr, tm_=float(t),
                     dst_=out.addr)


_vk_local = threading.local()


def _vk_kernels(dev, ctype, block, idx='int'):
    """The Vulkan kernels' pipelines for a device and code type, compiled once
    per thread (as the device: emsdev's slang_device is one per thread; slangpy
    keeps the SPIR-V on disk too). Kept thread-local, so that a thread's device
    and pipelines go with it."""
    from . import kernels_vk
    cache = getattr(_vk_local, 'cache', None)
    if cache is None:
        cache = _vk_local.cache = {}
    key = (id(dev), ctype, block, idx)
    if key not in cache:
        mod = dev.load_module_from_source(f"gfdtd_{ctype}_{block}_{idx}",
                                          kernels_vk.source(ctype, block, idx))
        cache[key] = (dev, {
            n: dev.create_compute_pipeline(
                dev.link_program([mod], [mod.entry_point(n)]))
            for n in kernels_vk.NAMES
        })
    return cache[key][1]


def _post_disjoint(op):
    """Whether the post kernel may run the excitation, lumped and Mur entries at
    once: their edges disjoint (and Mur's inner edges apart from the others)."""
    sets = [np.asarray(op.exc[0]), np.array([e for e, _ in op.par_L], np.int64),
            np.array([e[0] for e in op.series], np.int64)]
    if op.mur is not None:
        sets.append(np.concatenate(op.mur[:2]))
    flat = np.concatenate([np.unique(x) for x in sets])
    # (an edge twice in the lumped branches' own lists counts too)
    return (len(np.unique(flat)) == len(flat)
            and all(len(np.unique(x)) == len(x) for x in sets[1:3]))


def gpu_bytes(op):
    """About what the GPU holds of operator op (R-D7): the two fields (24 B a
    node, float32), the node codes and slots, the PML's auxiliary fields, the
    ferrite's, and 64 MB for the tables, the kernels and the rest."""
    n = int(np.prod(op.shape))
    code = 1 if n < 2 ** 8 else 2  # (its codes' type: a guess before compress)
    sh = tuple(op.shape)
    # psi: E and H, two components, float32
    pml = sum(16 * int(sl['n'])
              * int(np.prod([sh[b]
                             for b in range(3)
                             if b != sl['axis']]))
              for sl in (op.pml or []))
    fe = getattr(op, 'ferrite', None)
    # the ferrite: 56 B a cell along z (edges, coefficients, state, change),
    # 92 along its own direction, 36 an edge
    fer = fe.nbytes() if fe is not None else 0
    return 24 * n + code * n + 4 * n + pml + fer + 64e6


def fits(op, free, where='the GPU'):
    """MemoryError where operator op will not fit in `free` bytes of GPU memory
    (None: not known, not checked); ValueError past the GPU kernels' nodes."""
    need = gpu_bytes(op)
    if free is not None and need > 0.92 * free:
        raise MemoryError(
            f"gfdtd: the model needs about {need / 1e9:.2f} GB on {where}, "
            f"which has {free / 1e9:.2f} GB free: a coarser mesh, a smaller "
            "region, or the CPU (GFDTD_BACKEND=cpu)"
        )
    n = int(np.prod(op.shape))
    if 3 * n >= 2 ** 31:  # the kernels decode nodes in 32-bit arithmetic
        raise ValueError(
            f"gfdtd: {n / 1e6:.0f} M nodes - the GPU kernels take up to 715 M")


def make_backend(op, backend='auto', dtype=np.float32):
    """The backend for op: `backend` as resolve_backend names it. auto (or gpu
    not named outright) whose GPU has too little memory for the model runs on
    the CPU instead, its reason in the backend's `fallback`."""
    name = resolve_backend(backend)
    kinds = {'vulkan': VulkanBackend, 'core': CoreBackend,
             'numpy': NumpyBackend}
    # (the compiled H update is geometry alone; their ferrite the faces'
    # scheme alone)
    fe = getattr(op, 'ferrite', None)
    why = ("magnetic materials (Mue / Sigma)" if getattr(op, 'magnetic', False)
           else "a ferrite on the cells' centres alone (scheme 'centre')"
           if fe is not None and not getattr(fe, 'faces', False) else None)
    if why and name != 'numpy':
        be = NumpyBackend(op, dtype)
        be.fallback = f"{why}: running on NumPy's backend, not {name}"
        return be
    chosen = devices.preference(backend, 'GFDTD_BACKEND', ALIASES)
    try:
        return kinds[name](op, dtype)
    except MemoryError as e:
        if chosen != 'auto' or name != 'vulkan':
            raise
        cpu = resolve_backend('cpu')
        be = kinds[cpu](op, dtype)
        be.fallback = f"{e}: running on the CPU ({cpu})"
        return be


def _fmt_t(s):
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s" if s >= 3600 else (
        f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s")


class Engine:
    def __init__(self, op, backend='auto', dtype=np.float32):
        self.op = op
        self.be = make_backend(op, backend, dtype)

    def run(self, path, nr_ts, end_crit, verbose=1, out=print, max_time=None,
            stop=None):
        """Run up to nr_ts timesteps in folder `path` (probe files written
        there), ending when the energy (the field's and the lumped branches',
        backend.energy) falls below end_crit times its peak after the
        excitation. Returns dict(steps, seconds, reason, db)."""
        op, be = self.op, self.be
        dt, sig, sub = op.dt, op.signal, op.sub
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, 'et'), 'w', encoding='utf-8') as fh:
            fh.write(
                "% time-domain excitation signal by gfdtd\n% t/s\tvoltage\n")
            fh.writelines(
                f"{n * dt:.12g}\t{v:.12g}\n" for n, v in enumerate(sig))
        # the probe files, with openEMS's headers: one per probe, a field
        # probe's three components in one (t Ex Ey Ez)
        outs = []  # (file, [buffer columns], half-step offset)
        groups = {}
        for c, p in enumerate(be.order):
            if p.get('group') is not None and p['group'] in groups:
                outs[groups[p['group']]][1].append(c)
                continue
            fh = open(os.path.join(path, p['name']), 'w', encoding='utf-8')
            u = p['unit']
            if p.get('group') is not None:
                groups[p['group']] = len(outs)
                f = 'E' if p['field'] == 'V' else 'H'
                fh.write(
                    f"% time-domain {f}-field probe by gfdtd @{time.ctime()}\n")
                xyz, ijk = p['start']
                fh.write(
                    f"% coordinates: ({','.join(f'{v * u:g}' for v in xyz)}) m "
                    f"-> [{','.join(map(str, ijk))}]\n"
                )
                fh.write(f"% t/s\t{f}x\t{f}y\t{f}z\n")
            else:
                what = 'voltage' if p['field'] == 'V' else 'current'
                fh.write(
                    f"% time-domain {what} integration by gfdtd "
                    f"@{time.ctime()}\n"
                )
                for end, (xyz, ijk) in (('start', p['start']), ('stop',
                                                                p['stop'])):
                    fh.write(
                        f"% {end}-coordinates: "
                        f"({','.join(f'{v * u:g}' for v in xyz)}) m -> "
                        f"[{','.join(map(str, ijk))}]\n"
                    )
                fh.write(f"% t/s\t{what}\n")
            outs.append((fh, [c], 0.5 if p['field'] == 'I' else 0.0))
        dumps = Dumps(op, be, path, op.notes)
        pending = []  # (step, slot) samples in the device buffer

        def flush():
            if not pending:
                return
            rows = be.fetch(len(pending))
            # I sits half a step before V (its first sample at -dt / 2: readers
            # take dt from t1 - t0)
            for fh, cols, half in outs:
                fh.writelines(f"{(st - half) * dt:.12g}\t"
                              + "\t".join(f"{rows[r, c]:.12g}"
                                          for c in cols) + "\n"
                              for r, st in enumerate(pending))
                fh.flush()
            pending.clear()

        be.probe(0)
        pending.append(0)
        if dumps:
            dumps.sample(0)
        abort = os.path.join(path, 'ABORT')
        t0 = last = time.time()
        emax, e, reason, s, last_s = 0.0, 0.0, 'steps', 0, 0
        emax_driven = float('inf')
        nexc = len(sig)
        nr_ts = int(nr_ts)
        try:
            while s < nr_ts:
                s += 1
                be.step(sig[s] if s < nexc else None, s)
                if s % sub == 0:
                    be.probe(len(pending))
                    pending.append(s)
                    if dumps:
                        dumps.sample(s)
                    if len(pending) == CHUNK:
                        flush()
                if s % CHECK_STEPS == 0 or s == nr_ts:
                    e = be.energy()
                    emax = max(emax, e)
                    if s <= nexc + getattr(op, 'delay_max', 0):
                        # the peak while the excitation drives it
                        emax_driven = emax
                    now = time.time()
                    if verbose and now - last >= STATUS_S:
                        flush()
                        db = (10 * np.log10(e / emax)
                              if emax > 0 and e > 0 else float('-inf'))
                        rate = op.cells * (s - last_s) / (now - last) / 1e6
                        out(f"[@{_fmt_t(now - t0):>10}] Timestep: {s:>12d} || "
                            f"Speed: {rate:6.1f} MC/s "
                            f"({(now - last) / max(s - last_s, 1):.3e} s/TS) "
                            f"|| Energy: ~{e:.2e} ({db:.2f}dB)"
                            )
                        last, last_s = now, s
                    after = s > nexc + getattr(op, 'delay_max', 0)
                    # growing, not yet infinite
                    if (not np.isfinite(e)
                            or (after and e > GROWTH * emax_driven)):
                        reason = 'unstable'
                        break
                    if after and emax > 0 and e < end_crit * emax:
                        reason = 'end'
                        break
                    # (stop: SetAbort's flag)
                    if os.path.exists(abort) or (stop is not None and stop()):
                        reason = 'abort'
                        break
                    # openEMS's MaxTime: the run's own time
                    if max_time and now - t0 >= max_time:
                        reason = 'max_time'
                        break
            be.sync()
            flush()
            written = dumps.close()
        finally:
            for fh, _, _ in outs:
                fh.close()
        sec = time.time() - t0
        db = 10 * np.log10(e / emax) if emax > 0 and e > 0 else float('-inf')
        if reason == 'end':
            out(f"RunFDTD: end-criteria of {10 * np.log10(end_crit):.2f}dB "
                f"reached after {s} timesteps ({db:.2f}dB)"
                )
        elif reason == 'abort':
            out(f"RunFDTD: aborted (ABORT file or SetAbort) after {s} "
                f"timesteps ({db:.2f}dB)"
                )
        elif reason == 'max_time':
            out(f"RunFDTD: max. simulation time ({max_time:g} s) reached after "
                f"{s} timesteps ({db:.2f}dB)"
                )
        elif reason == 'unstable':
            out(f"RunFDTD: WARNING: the fields grew without bound after {s} "
                "timesteps - unstable"
                )
        else:
            out(f"RunFDTD: max. number of timesteps ({nr_ts}) reached "
                f"({db:.2f}dB)"
                )
        out(f"Time for {s} iterations with {op.cells:.2f} cells : {sec:.2f} sec"
            )
        out(f"Speed: {op.cells * s / max(sec, 1e-9) / 1e6:.2f} MCells/s ")
        return dict(steps=s, seconds=sec, reason=reason, db=db, dumps=written)
