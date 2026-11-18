"""
mom25d.gpu - GPU dense LU for the mixed-precision direct solver, through Vulkan
(gpu_vk.py: any Vulkan GPU, AMD, Intel or NVIDIA)

The matrix is still filled on the CPU (the C++ core takes ~2 s); the expensive
O(N^3) part, the complex64 LU factorisation, runs on the GPU. The refinement's
products are in double precision, so results match the CPU solver: on the GPU
where it holds the double matrix too (put_dense, mul_dense; policy.GPU_DENSE),
else on the CPU. A factor whose GPU triangular solves lose their accuracy is
downloaded and solved on the CPU.

Backends (force one with MOM25D_GPU=vulkan|emulate|off; 'auto' is Vulkan):
  vulkan   slangpy on any Vulkan GPU (gpu_vk.py): a hybrid LU, the panels on
           the CPU (LAPACK), the updates on the GPU. LU in place: VRAM ~ 8 N^2
           bytes (16 GB: ~41k unknowns). On an RX 7800 XT it factors faster
           than rocSOLVER from N = 4096.
  emulate  NumPy stand-in with the same interface (tests / development without
           a GPU).
"""
import numpy as np
import scipy.linalg as sla
# the device and its memory: emsdev, shared with gfdtd
from kicad2ems.emsdev import gpu as devices


GPUUnavailable = devices.Unavailable


class EmulatedBackend:
    """NumPy/SciPy implementation of the backend interface (no GPU; for
    tests)."""
    name, device, api, hip = 'emulate', 'CPU emulation', 'none', False
    lu = piv = None

    def __init__(self, mem=64e9):
        self._mem = mem

    def mem_free(self):
        return float(self._mem)

    mem_total = mem_free

    def vram_needed(self, N):
        return 8.0 * N * N + 0.4e9

    def fits(self, N, extra=0.0):
        from .policy import gpu_fits
        return gpu_fits(self.vram_needed(N), self.mem_free() + extra)

    def release(self):
        self.lu = self.piv = None

    def factor(self, Z):
        self.lu, self.piv = sla.lu_factor(np.array(Z.T, order='F'),
                                          overwrite_a=True, check_finite=False)

    def solve(self, R):
        return sla.lu_solve((self.lu, self.piv), np.asarray(R, np.complex64),
                            trans=1, check_finite=False).astype(complex)

    def download(self):
        return self.lu, self.piv

    zd = None

    def dense_ok(self):
        return True

    def host_buffer(self, nbytes):
        return _Host(nbytes)

    def put_dense(self, Z, host=None):
        self.zd = Z

    def drop_dense(self):
        self.zd = None

    def mul_dense(self, V):
        return self.zd @ V

    def factor_dense(self, dsc):
        self.factor((dsc[:, None] * self.zd * dsc).astype(np.complex64))


class _Host:
    """EmulatedBackend.host_buffer's: host memory, HostBuffer's view."""
    def __init__(self, nbytes):
        self.nbytes, self.array = int(nbytes), np.zeros(int(nbytes), np.uint8)

    def view(self, dtype, count=None):
        a = self.array.view(dtype)
        return a if count is None else a[:count]


def _vulkan():
    from .gpu_vk import VulkanBackend
    return VulkanBackend()


_BACKENDS = {'vulkan': _vulkan, 'emulate': EmulatedBackend}
_cache = {}


def get_backend(pref='auto'):
    """Return a backend instance or None. pref: 'auto' | 'vulkan' | 'emulate' |
    'off'. The environment variable MOM25D_GPU overrides pref.
    Reasons for failures are kept in get_backend.why for logging."""
    pref = devices.preference(pref, 'MOM25D_GPU')
    get_backend.why = []
    if pref == 'off':
        return None
    names = ['vulkan'] if pref == 'auto' else [pref]
    for n in names:
        if n not in _BACKENDS:
            get_backend.why.append(f"unknown GPU backend {n!r}")
            continue
        try:
            if n not in _cache:
                _cache[n] = _BACKENDS[n]()
            return _cache[n]
        except GPUUnavailable as e:
            get_backend.why.append(str(e))
        except ImportError as e:  # vulkan: no slangpy
            get_backend.why.append(f"{n}: {e}")
    return None


get_backend.why = []


def describe():
    """One-line status for logs / --gpu-info."""
    b = get_backend()
    if b is None:
        return "GPU: none (" + "; ".join(get_backend.why or ['disabled']) + ")"
    return (f"GPU: {b.device} via {b.name} ({b.api}), {b.mem_free() / 1e9:.1f} "
            "GB free")
