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


def test_port_watch_stops_a_decayed_run(tmp_path):
    """PortWatch writes ABORT once every port signal is end_db below its peak after the pulse, and records
    why; a signal still ringing keeps the run going."""
    import json, time, numpy as np
    from kicad2openems.simulation import PortWatch, port_decay, pulse_time
    t = np.arange(0, 20e-9, 10e-12)
    ring = np.exp(-((t - 1e-9) / 0.3e-9) ** 2) + 1e-3 * np.sin(2 * np.pi * 3e9 * t)     # -60 dB ripple: still on
    for name, sig in (('port_ut_1', ring), ('port_it_1', ring / 50), ('port_ut_2', 0.5 * ring), ('port_it_2', ring / 100)):
        np.savetxt(tmp_path / name, np.column_stack([t, sig]), header='t u', comments='% ')
    level = port_decay(str(tmp_path), 2e-9)
    assert level[0] == pytest.approx(t[-1]) and -62 < level[1] < -55
    w = PortWatch(str(tmp_path), end_db=-40, t_min=2e-9, window=2e-9, interval=0.05).start()
    time.sleep(0.4)
    w.stop()
    assert (tmp_path / 'ABORT').exists()
    assert json.loads((tmp_path / 'run_end.json').read_text())['reason'] == 'ports'
    (tmp_path / 'ABORT').unlink()
    w = PortWatch(str(tmp_path), end_db=-70, t_min=2e-9, window=2e-9, interval=0.05).start()
    time.sleep(0.3)
    w.stop()
    assert not (tmp_path / 'ABORT').exists()                        # -60 dB is not -70 dB
    assert pulse_time(1e9, 6e9) == pytest.approx(2 * 9 / (2 * np.pi * 2.625e9))
