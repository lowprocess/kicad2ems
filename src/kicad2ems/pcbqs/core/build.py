"""Build the optional QS backend: python -m kicad2ems.pcbqs.core.build."""
from kicad2ems.emsdev.build import NATIVE, build_core
build_core(__file__, "pcbqs_core.cpp", arch_env="PCBQS_ARCH", includes_here=True, includes=(NATIVE,))
