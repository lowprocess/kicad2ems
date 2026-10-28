# Compiled cores

The C++ kernels the solvers build on install (`python -m mom25d.core.build`, `python -m gfdtd.core.build`), from
their sources through Doxygen. Without a built core each falls back to NumPy.

## mom25d

The method of moments' kernels: the singular and near-singular integrals and the matrix fill (OpenMP).

```{doxygenfile} mom25d_core.cpp
```

## gfdtd

The FDTD update, shared by the GPU kernels (CuPy) and the CPU core.

```{doxygenfile} fdtd_core.h
```

```{doxygenfile} gfdtd_core.cpp
```
