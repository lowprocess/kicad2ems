"""run.s_column: S from a run's port waves is in power waves at each port's own
z0, as mom25d's is."""
import numpy as np
import pytest
from kicad2ems.fdtd.run import s_column


class _Port:
    def __init__(self, inc, ref):
        self.uf_inc, self.uf_ref = np.atleast_1d(inc), np.atleast_1d(ref)


def test_a_matched_thru_between_unlike_ports_is_lossless_and_reciprocal():
    """A matched lossless thru from a 50 to a 75 ohm port: power waves a = b =
    1 are voltage waves of sqrt(z0). Its S21 and S12 are both 1 (the voltage
    waves' ratio had read 1.22 and 0.82: not passive, not reciprocal)."""
    z0 = [50.0, 75.0]
    # port 1 excited: a1 = 1, b2 = 1; port 2 excited: a2 = 1, b1 = 1
    run1 = [_Port(np.sqrt(50), 0), _Port(0, np.sqrt(75))]
    run2 = [_Port(0, np.sqrt(50)), _Port(np.sqrt(75), 0)]
    S = np.stack([s_column(run1, 0, z0), s_column(run2, 1, z0)], axis=-1)
    assert S[0] == pytest.approx(np.array([[0, 1], [1, 0]]))


def test_ports_of_one_z0_keep_the_voltage_waves_ratio():
    ports = [_Port([1, 2], [0.1, 0.2j]), _Port([0, 0], [0.5, -0.3])]
    col = s_column(ports, 0, [50.0, 50.0])
    assert col == pytest.approx(np.array([[0.1, 0.5], [0.1j, -0.15]]))
