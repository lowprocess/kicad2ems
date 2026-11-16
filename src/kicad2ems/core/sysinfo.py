"""
What this machine offers a run: the solvers (each one's check), the GPUs they would use (each one's resources())
and the memory of the system and of the GPU. The GUI's System panel runs it as its own process (python -m
kicad2ems.sysinfo: JSON on stdout), so the GUI never holds a GPU context or imports CuPy.

    info()      dict(solvers=[dict(name, label, ok, what)], resources={solver: dict(gpus, why)},
                     gpu=dict(...) | None, gpu_why, ram=dict(total, available), cpus, threads)
    core_threads()  the worker count of a parallel map: one per fast physical core (emsdev)
"""
import json
import os
import sys

from emsdev.cpu import core_threads                              # noqa: F401  (the KiCad plugin's map workers)


def memory():
    """System memory in GB: installed, and available to this process now (cgroup aware, as the solvers see it)."""
    from emsdev import memory as mem
    total = mem.total()
    return dict(total=total / 1e9 if total else None, available=mem.available() / 1e9)


def resources():
    """{solver: dict(gpus, why)}: what each installed solver would run on here (Solver.resources); a solver that
    answers None runs on the CPU only and is left out."""
    from .solvers import registry
    out = {}
    for n, sv in registry().items():
        try:
            r = sv.resources()
        except Exception as e:                                       # a probe never stops the listing
            r = dict(gpus=[], why=f"{type(e).__name__}: {e}")
        if r is not None:
            out[n] = r
    return out


def gpu(res=None):
    """(dict(device, backend, api, free, total) in GB, or None; why there is none): the GPU the solvers would use,
    mom25d's first (the System panel's memory check is about its LU), else any solver's."""
    from .solvers import MOM
    res = resources() if res is None else res
    for n in [MOM] + [n for n in res if n != MOM]:
        if res.get(n, {}).get('gpus'):
            return res[n]['gpus'][0], ''
    return None, '; '.join(f"{n}: {r['why']}" for n, r in res.items() if r.get('why')) or 'no solver uses a GPU'


def solvers():
    from .solvers import registry
    out = []
    for n, sv in registry().items():
        try:
            ok, what = sv.check()
        except Exception as e:                                       # a check never stops the listing
            ok, what = False, f"{type(e).__name__}: {e}"
        out.append(dict(name=n, label=sv.label, ok=bool(ok), what=what, consumes=sv.consumes))
    return out


def info():
    res = resources()
    g, why = gpu(res)
    from .formats import stamp
    return stamp(dict(solvers=solvers(), resources=res, gpu=g, gpu_why=why, ram=memory(), cpus=os.cpu_count(),
                      threads=core_threads()), 'kicad2ems-sysinfo')


if __name__ == '__main__':
    json.dump(info(), sys.stdout)
