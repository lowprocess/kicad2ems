"""
Checks of the simulation against itself and against the datasheet figures it
was built from.  About 20 s:

    python -m pytest designs/spectrum_analyzer/test_sasim.py
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sasim import Analyzer, Signal, lo  # noqa: E402
from sasim.rf import Tones, compression_db  # noqa: E402

GHZ, MHZ = 1e9, 1e6


@pytest.fixture(scope="module")
def sa():
    return Analyzer()


def peak(f, p, at, span=40e3):
    return float(p[np.abs(f - at) <= span].max())


def test_lmx2582_profile_is_the_datasheets():
    s = lo.lmx2582(loop_bw=1.0)        # open loop, as table 6.5 is
    for f, off, row in ((0.9, 1e4, -105.7), (0.9, 1e5, -129.8),
                        (0.9, 1e6, -150.4), (0.9, 1e7, -160.6),
                        (1.8, 1e5, -123.6), (1.8, 1e6, -144.5),
                        (1.8, 1e7, -157.2), (5.5, 1e5, -114.0),
                        (5.5, 1e6, -134.9), (5.5, 1e7, -151.3)):
        assert s.phase_noise(f * GHZ, off) == pytest.approx(row, abs=1.3)
    # locked: the PLL's noise inside the loop, the VCO's outside
    s = lo.lmx2582()
    assert s.phase_noise(5.5 * GHZ, 1e4) < -95.0
    assert s.phase_noise(5.5 * GHZ, 1e7) == pytest.approx(-151.3, abs=0.3)
    assert s.divider(3.2 * GHZ) == 2 and s.divider(4.0 * GHZ) == 1
    with pytest.raises(ValueError):
        s.check(5.6 * GHZ)


def test_tones_merge_and_prune():
    t = Tones([1e9, 1e9, 2e9], [-10.0, -10.0, -250.0], ["a", "b", "c"])
    t = t.pruned()
    assert len(t) == 1
    assert t.p[0] == pytest.approx(-6.99, abs=0.01)
    assert compression_db(1.0, 1.0) == pytest.approx(1.0, abs=0.01)
    assert compression_db(-30.0, 1.0) < 0.01


@pytest.mark.parametrize("f", [100.3 * MHZ, 1.5002 * GHZ, 3.0001 * GHZ,
                               5.0003 * GHZ])
def test_a_carrier_reads_its_level_in_every_band(sa, f):
    fc = sa.centres(f, f)[0]
    fin, p, info = sa.capture([Signal(f, -40.0)], fc)
    assert peak(fin, p, f) == pytest.approx(-40.0, abs=0.5)
    assert info["clipped"] == 0.0


@pytest.mark.parametrize("f", [1.0 * GHZ, 4.0 * GHZ])
def test_the_noise_floor_is_the_budgets(sa, f):
    fin, p, info = sa.capture([], f)
    _, s = sa.budget(f)
    mean = 10 * np.log10(np.mean(10 ** (p / 10)))
    assert mean == pytest.approx(
        s["danl_dbm_hz"] + 10 * np.log10(info["enbw"]), abs=1.5)
    # the second mixer's image is in the noise: 3 dB over the cascade's
    assert 2.0 < s["nf_effective"] - s["nf_friis"] < 5.0


@pytest.mark.parametrize("f", [1.0002 * GHZ, 4.0002 * GHZ])
def test_the_image_is_there_and_two_captures_remove_it(sa, f):
    sig = [Signal(f, -40.0)]
    image = f - 2 * sa.if2
    fin, one, _ = sa.sweep(sig, image - 5 * MHZ, f + 5 * MHZ,
                           image_reject=False, workers=1)
    _, two, _ = sa.sweep(sig, image - 5 * MHZ, f + 5 * MHZ,
                         image_reject=True, workers=1)
    # (it is 20 MHz below to within the signal's place in its step)
    assert peak(fin, one, image, 1 * MHZ) == pytest.approx(-40.0, abs=1.0)
    assert peak(fin, two, image, 1 * MHZ) < -80.0
    assert peak(fin, two, f) == pytest.approx(-40.0, abs=0.5)


def test_third_order_products_follow_the_budget():
    sa = Analyzer(att_db=0.0, nfft=65536)
    f1, f2, lv = 1000.2 * MHZ, 1001.2 * MHZ, -26.0
    fin, p, info = sa.capture([Signal(f1, lv), Signal(f2, lv)], 1.0 * GHZ)
    _, s = sa.budget(1.0 * GHZ)
    im3 = max(peak(fin, p, 2 * f1 - f2, 3e3), peak(fin, p, 2 * f2 - f1, 3e3))
    # the budget's cascade is the worst case (every product in phase)
    assert -6.0 < (im3 - lv) - 2 * (lv - s["iip3"]) < 2.0


def test_an_overload_clips_the_adc_and_reads_low(sa):
    fin, p, info = sa.capture([Signal(1000.2 * MHZ, +5.0)], 1.0 * GHZ)
    assert info["clipped"] > 0.05
    assert peak(fin, p, 1000.2 * MHZ) < 3.0


def test_a_noise_like_signal_reads_its_density(sa):
    bw, lv = 18 * MHZ, -40.0
    fin, p, info = sa.sweep([Signal(2.442 * GHZ, lv, bw=bw)], 2.436 * GHZ,
                            2.448 * GHZ, image_reject=False, workers=1)[0:3]
    mean = 10 * np.log10(np.mean(10 ** (p / 10)))
    enbw = sa.capture([], 2.44 * GHZ)[2]["enbw"]
    assert mean == pytest.approx(lv - 10 * np.log10(bw / enbw), abs=1.5)


@pytest.mark.parametrize("f", [100.3 * MHZ, 1.0002 * GHZ, 4.0002 * GHZ])
def test_the_preamps_buy_noise_figure_with_linearity(sa, f):
    quiet, loud = Analyzer(att_db=0.0), Analyzer(att_db=0.0, preamp=True)
    _, off = quiet.budget(f)
    _, on = loud.budget(f)
    assert off["nf_effective"] - on["nf_effective"] > 10.0
    assert off["iip3"] - on["iip3"] > 15.0
    fc = loud.centres(f, f)[0]
    fin, p, info = loud.capture([Signal(f, -70.0)], fc)
    assert peak(fin, p, f) == pytest.approx(-70.0, abs=0.5)
