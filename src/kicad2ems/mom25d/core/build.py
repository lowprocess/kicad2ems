"""Build the optional compiled core mom25d._core:  python -m
kicad2ems.mom25d.core.build   (needs pybind11 and g++ with OpenMP, or on Windows
MSVC: emsdev.build; also: python src/mom25d/core/build.py in a checkout). The
kernels fall back to NumPy when the module is absent; the mesher needs it
(CDT)."""
from kicad2ems.emsdev.build import NATIVE, build_core

# the mesher's CDT
build_core(__file__, 'mom25d_core.cpp', arch_env='MOM25D_ARCH',
           includes=(NATIVE, ))
