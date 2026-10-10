"""build_fdtd against strict stand-ins for CSXCAD / openEMS: every scalar handed over must be a plain
Python number (older CSXCAD releases test type(v) in (int, float) and reject numpy scalars)."""
import numpy as np
import pytest
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.boundaries import resolve_bc
from kicad2openems.mesh import auto_mesh


@pytest.mark.parametrize('sigma', [None, 5.8e7])
def test_build_fdtd_passes_plain_scalars(demo_files, fake_openems, sigma):
    from kicad2openems.simulation import build_fdtd
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, log=lambda *a: None)
    freq = np.linspace(1e9, 8e9, 11)                                # numpy scalars, as in a real run
    build_fdtd(m, mesh, freq[0], freq[-1], 0, bc=bc, sigma=np.float64(sigma) if sigma else None)
    assert fake_openems.count('linpoly') == len(m.layers) and fake_openems.count('port') == 2
    assert fake_openems.count('cyl') == len(m.vias)


def test_build_fdtd_wideband_dielectric(demo_files, fake_openems):
    from kicad2openems.simulation import build_fdtd
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, log=lambda *a: None)
    build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, diel='wideband', diel_fref=1e9)
    debye = [x for x in fake_openems if isinstance(x, tuple) and x[0] == 'debye']
    assert len(debye) == len(m.layers) and all(order >= 3 for _, _, order in debye)
    assert fake_openems.count('linpoly') == len(m.layers)
