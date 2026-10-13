"""
What this machine offers a run: the solvers (each one's check), the GPU mom25d would use and the memory of
the system and of the GPU. The GUI's System panel runs it as its own process (python -m kicad2ems.sysinfo:
JSON on stdout), so the GUI never holds a GPU context or imports CuPy.

    info()      dict(solvers=[dict(name, label, ok, what)], gpu=dict(...) | None, gpu_why, ram=dict(total, available))
"""
import json
import os
import sys


def memory():
    """System memory in GB: installed, and available to this process now (cgroup aware, as mom25d sees it)."""
    from .pipeline import ram_gb
    try:
        from mom25d.sim import available_memory
        avail = available_memory() / 1e9
    except Exception:                                               # mom25d not installed
        avail = None
    return dict(total=ram_gb(), available=avail)


def gpu():
    """(dict(device, backend, api, free, total) in GB, or None; why there is none)."""
    try:
        from mom25d.gpu import get_backend
    except Exception as e:
        return None, f"mom25d: {type(e).__name__}: {e}"
    b = get_backend('auto')
    if b is None:
        return None, '; '.join(get_backend.why) or 'disabled (MOM25D_GPU=off)'
    total = b.mem_total() if hasattr(b, 'mem_total') else None
    return dict(device=b.device, backend=b.name, api='ROCm/HIP' if b.hip else 'CUDA', free=b.mem_free() / 1e9,
                total=total / 1e9 if total else None), ''


def solvers():
    from .solvers import shown
    out = []
    for n, sv in shown().items():                                   # the GUI's: experimental with --experimental
        try:
            ok, what = sv.check()
        except Exception as e:                                       # a check never stops the listing
            ok, what = False, f"{type(e).__name__}: {e}"
        out.append(dict(name=n, label=sv.label, ok=bool(ok), what=what, consumes=sv.consumes))
    return out


def info():
    g, why = gpu()
    return dict(solvers=solvers(), gpu=g, gpu_why=why, ram=memory(), cpus=os.cpu_count())


if __name__ == '__main__':
    json.dump(info(), sys.stdout)
