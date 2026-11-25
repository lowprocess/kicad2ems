"""IBIS buffers (kicad2ems.core.ibis): the file's numbers and sections, the
switching coefficients from [Ramp] and from two V-T tables, and a buffer as a
port's termination against what it must be (a resistive buffer is a source
behind its resistance; a receiver's C_comp; its package's resistance)."""
import numpy as np
import pytest

from kicad2ems.core import ibis as IB
from kicad2ems.core import macromodel as MM

VCC, RB = 1.8, 50.0


def _matched_port(z0=50.0):
    """A one-port of S 0: a z0 load (no poles)."""
    return MM.Model(np.zeros(0, complex), np.zeros((0, 1, 1), complex),
                    np.zeros((1, 1)), z0, 10e9)


def _line(z0=50.0, ee=3.5, length=0.05):
    from kicad2ems.core.deembed import abcd_to_s, line_abcd
    f = np.linspace(20e6, 20e9, 1000)
    return MM.fit(f, abcd_to_s(line_abcd(z0, ee, length, f, 0.0), 50.0))


def _table(name, rows):
    return f"[{name}]\n" + "\n".join(
        " ".join(str(x) for x in r) for r in rows) + "\n"


def _ibs(extra='', ramp=True, c_comp='0p 0p 0p', kind='Output',
         polarity='Non-Inverting', package='', pins=''):
    """A file of a resistive buffer: pull-up and pull-down RB ohm to VCC and
    to ground, no clamps."""
    v = np.linspace(-VCC, 2 * VCC, 7)
    pd = [(f"{x:.4f}", f"{x / RB:.6f}", 'NA', 'NA') for x in v]
    pu = [(f"{x:.4f}", f"{-x / RB:.6f}", 'NA', 'NA') for x in v]
    r = ("[Ramp]\ndV/dt_r 0.54/100p NA NA\ndV/dt_f 0.54/100p NA NA\n"
         "R_load = 50\n") if ramp else ''
    return (f"[IBIS Ver] 4.2\n| a comment\n[File_Name] test.ibs\n"
            f"[Component] TEST\n[Manufacturer] none\n{package}"
            f"[Pin] signal_name model_name R_pin L_pin C_pin\n{pins}"
            f"1 DQ DRV\n2 IN RX\n"
            f"[Model] DRV\nModel_type {kind}\nPolarity {polarity}\n"
            f"C_comp {c_comp}\n[Voltage Range] {VCC} {VCC} {VCC}\n"
            + _table('Pulldown', pd) + _table('Pullup', pu) + r + extra
            + f"[Model] RX\nModel_type Input\nC_comp 2p NA NA\n"
            f"[Voltage Range] {VCC} NA NA\n[End]\n")


def test_numbers():
    assert IB.number('1.2nH') == pytest.approx(1.2e-9)
    assert IB.number('50ohm') == 50.0 and IB.number('3.3V') == 3.3
    assert IB.number('1M') == 1e6 and IB.number('2m') == pytest.approx(2e-3)
    assert IB.number('-.5') == -0.5 and IB.number('1e-3') == 1e-3
    assert IB.number('NA') is None


def test_the_file_s_sections():
    lib = IB.parse(_ibs(package="[Package]\n| variable typ min max\n"
                        "R_pkg 0.2 0.1 0.3\nL_pkg 2nH NA NA\nC_pkg 0.5pF NA "
                        "NA\n",
                        pins="3 DQ2 DRV 0.4 3n 0.6p\n"))
    assert set(lib.models) == {'DRV', 'RX'}
    b = lib.model('DRV')
    assert b.kind == 'output' and b.vcc() == VCC
    v, I = b.tables['pulldown']
    assert len(v) == 7 and np.allclose(I[:, 1], I[:, 0])   # NA: typ's
    assert b.ramp['r'][0] == pytest.approx((0.54, 100e-12))
    assert b.package == pytest.approx((0.2, 2e-9, 0.5e-12))
    assert lib.model('DRV', corner='max').package[0] == 0.3
    assert lib.model(None, pin='3').package == pytest.approx(
        (0.4, 3e-9, 0.6e-12))
    assert lib.model('RX').value('c_comp') == pytest.approx(2e-12)
    with pytest.raises(IB.IbisError, match='no model'):
        lib.model('X')


def test_a_resistive_buffer_is_a_source_behind_its_resistance():
    """The resistive buffer (no C_comp, no package) from [Ramp] driving a
    50 mm matched line into 50 ohm: the far end as a Thevenin source of Ku(t)
    VCC behind RB drives it, to 1e-6; its edge into R_load the [Ramp]'s 20-80
    % slope within 2 %."""
    m = _line()
    b = IB.parse(_ibs()).model('DRV')
    bits = [0, 0, 1, 1, 0, 1, 0, 0]
    ui, t0 = 0.4e-9, 1e-9
    t = np.arange(0, t0 + len(bits) * 0.4e-9 + 1e-9, 1e-12)
    drv = IB.Driver(b, bits, ui, t0=t0)
    V, _ = MM.simulate(m, [drv, MM.Load(50.0)], t)
    ref = IB.Driver(b, bits, ui, t0=t0)       # (the same Ku, as a source)
    th = MM.Thevenin(lambda tt: VCC * ref.k(tt)[0], RB)
    W, _ = MM.simulate(m, [th, MM.Load(50.0)], t)
    assert np.abs(V - W).max() < 1e-6
    # its edge into R_load (50 ohm to ground): 0.54 V in 100 ps, 20-80 %
    one = IB.Driver(b, [0, 1], 0.5e-9)          # (the edge at 0.5 ns)
    tt = np.arange(0, 2.5e-9, 0.5e-12)
    U, _ = MM.simulate(_matched_port(), [one], tt)
    u, hi = U[:, 0], U[-1, 0]
    t20, t80 = (tt[np.argmax(u >= x * hi)] for x in (0.2, 0.8))
    assert 0.6 * hi / (t80 - t20) == pytest.approx(0.54 / 100e-12, rel=0.02)


def test_two_waveforms_give_both_coefficients():
    """V-T tables into 50 ohm to ground and to VCC made from a Ku and a Kd
    that are not 1 - each other (the pull-down off faster): both found again
    within 1e-3 (one table, or [Ramp], must take Kd = 1 - Ku)."""
    ts = np.linspace(0, 0.5e-9, 51)
    ku = 0.5 * (1 - np.cos(np.pi * ts / ts[-1]))
    kd = (1 - ku) ** 2
    def wave(vf):
        # the die into rf to vf: ku (vcc - v) / RB - kd v / RB = (v - vf) / rf
        rf = 50.0
        v = (ku * VCC / RB + vf / rf) / (ku / RB + kd / RB + 1 / rf)
        rows = "\n".join(f"{t:.6e} {x:.6f} NA NA" for t, x in zip(ts, v))
        return (f"[Rising Waveform]\nR_fixture = 50\nV_fixture = {vf}\n"
                f"{rows}\n")
    lib = IB.parse(_ibs(extra=wave(0.0) + wave(VCC), ramp=False))
    b = lib.model('DRV')
    t, Ku, Kd = IB._switching(b, IB._Curves(b, 'typ'), 'rising', 'typ', 0.0)
    assert np.abs(np.interp(ts, t, Ku) - ku).max() < 1e-3
    assert np.abs(np.interp(ts, t, Kd) - kd).max() < 1e-3


def test_a_receiver_s_c_comp():
    """A matched line's far end at an IBIS input of C_comp 2 pF: it charges
    to twice the incident wave (an open), its 10-90 % rise 2.2 RC = 220 ps
    (RC: the line's 50 ohm and 2 pF) less the edge's own, within 10 %."""
    m = _line()
    lib = IB.parse(_ibs())
    rx = IB.Receiver(lib.model('RX'))
    rise = 20e-12
    step = lambda t: 0.5 * (1 - np.cos(np.pi * np.clip(t / (rise / 0.59),
                                                       0, 1)))
    t = np.arange(0, 3e-9, 0.5e-12)
    V, _ = MM.simulate(m, [MM.Thevenin(step, 50.0), rx], t)
    v = V[:, 1]
    assert v[-1] == pytest.approx(1.0, rel=2e-3)
    t10, t90 = (t[np.argmax(v >= x * v[-1])] for x in (0.1, 0.9))
    assert t90 - t10 == pytest.approx(2.2 * 50 * 2e-12, rel=0.10)


def test_a_package_s_resistance_and_an_inverted_buffer():
    """The driver behind a package (R 5 ohm, L 1 nH, C 0.5 pF) high into
    50 ohm: VCC 50 / (RB + 5 + 50) at the pin once settled; an inverting
    model the same bit low (0 V)."""
    pkg = ("[Package]\nR_pkg 5 NA NA\nL_pkg 1nH NA NA\nC_pkg 0.5p NA NA\n")
    for pol, want in (('Non-Inverting', VCC * 50 / (RB + 55)),
                      ('Inverting', 0.0)):
        b = IB.parse(_ibs(package=pkg, polarity=pol)).model('DRV')
        drv = IB.Driver(b, [0, 1], 0.5e-9)
        t = np.arange(0, 4e-9, 1e-12)
        V, _ = MM.simulate(_matched_port(), [drv], t)
        assert V[-1, 0] == pytest.approx(want, abs=2e-3), pol


def test_a_receiver_model_is_not_a_driver():
    with pytest.raises(IB.IbisError, match='not a driver'):
        IB.Driver(IB.parse(_ibs()).model('RX'), [0, 1], 1e-9)
