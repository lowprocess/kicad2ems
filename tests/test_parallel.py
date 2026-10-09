"""--jobs: excitations run in worker processes fill the same S columns as the serial loop (stub runs,
no openEMS needed)."""
import os
import numpy as np
from kicad2openems import simulation
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model


def fake_run_port(k, m, mesh, freq, bc, out_dir, dumps=(), xml_path=None, verbose=1, **kw):
    """Top level so that spawned workers can import it: column k = (k + 1) * (i + 1)."""
    n = len(m.ports)
    col = np.array([[(k + 1) * (i + 1) + 0j for i in range(n)]] * len(freq))
    return k, col, {}, None, 0.0, 100


def test_parallel_matches_serial(demo_files, tmp_path, monkeypatch):
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    monkeypatch.setattr(simulation, '_run_port', fake_run_port)
    freq = np.linspace(1e9, 2e9, 3)
    excite = list(range(len(m.ports)))
    log = []
    S1 = simulation.run_excitations(m, {}, freq, excite, None, str(tmp_path), jobs=1, log=log.append)
    S2 = simulation.run_excitations(m, {}, freq, excite, None, str(tmp_path), jobs=2, log=log.append)
    assert np.array_equal(S1, S2) and not np.isnan(S1).any()
    assert any('2 at a time' in l for l in log)
