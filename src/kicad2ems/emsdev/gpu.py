"""
The GPUs: which ones Vulkan (emsdev.vulkan) sees, what each is (name, memory),
and how a solver's backend setting reads
(preference). A solver keeps its own backends (mom25d's LU, gfdtd's update
kernels) and asks here which device they would run on.

    gpu.vulkan_device()  the best Vulkan GPU (emsdev.vulkan; slangpy runs the
                         solvers' kernels on it)
    gpu.devices()        every device of every library that has one: [Device]
    gpu.probe()          (devices, why): why lists what failed, one reason per
                         library
"""
import os
from dataclasses import dataclass, field

LIBRARIES = ('vulkan',)
OFF = {'0', 'false', 'no', 'off', 'none'}
ON = {'1', 'on', 'true', 'yes', 'auto', ''}


class Unavailable(RuntimeError):
    """A library has no usable GPU; the message says why."""


@dataclass
class Device:
    index: int
    name: str
    library: str                            # 'vulkan'
    api: str                                # 'Vulkan'
    total: float = None                     # bytes of device memory
    version: str = ''                       # the library's version
    _free: object = field(default=None, repr=False, compare=False)

    def free(self):
        """Bytes free on the device now (the library's cached blocks released
        first), None if it cannot tell."""
        return float(self._free()) if self._free else None

    def describe(self):
        mem = f", {self.total / 2 ** 30:.0f} GB" if self.total else ''
        # vulkan: 'slangpy x.y'
        lib = (self.version if self.library == 'vulkan' else
               f"{self.library} {self.version}")
        return f"{self.name} ({self.api}, {lib}{mem})".replace(' )',
                                                               ')').replace(
                                                                   ', ,', ',')

    def as_dict(self):
        """The JSON form kicad2ems's sysinfo reports (GB)."""
        free = self.free()
        return dict(device=self.name, backend=self.library, api=self.api,
                    index=self.index,
                    free=(free / 1e9 if free is not None else None),
                    total=(self.total / 1e9 if self.total else None))


def _name(v, default='GPU'):
    v = v or default
    return v.decode() if isinstance(v, bytes) else str(v)


def vulkan_device(index=None, need=()):
    """Device of Vulkan adapter `index` (None: the best, or
    EMSDEV_VULKAN_DEVICE's): emsdev.vulkan.device; `need`: the features a
    solver's kernels use (Unavailable where the GPU lacks one)."""
    from . import vulkan
    try:
        return vulkan.device(index, need)
    except vulkan.Unavailable as e:
        raise Unavailable(str(e))


def probe(libraries=LIBRARIES):
    """(devices, why): every device of each library, and what kept a library
    from giving any."""
    out, why = [], []
    for lib in libraries:
        try:
            out.append(vulkan_device(0))
        except Unavailable as e:
            why.append(str(e))
            continue
        from . import vulkan
        for i in range(1, len(vulkan.adapters())):
            try:
                out.append(vulkan_device(i))
            except Unavailable:
                pass
    return out, why


def devices(libraries=LIBRARIES):
    return probe(libraries)[0]


def preference(pref='auto', env=None, aliases=None):
    """A backend setting as a solver reads it: 'off', 'auto' or a backend's name
    (lower case). The environment variable `env`, when set, overrides pref;
    aliases maps other names onto backends (gfdtd: vk -> vulkan)."""
    p = os.environ.get(env, '') if env else ''
    p = str(p or pref or 'auto').strip().lower()
    if p in OFF:
        return 'off'
    if p in ON:
        return 'auto'
    return (aliases or {}).get(p, p)
