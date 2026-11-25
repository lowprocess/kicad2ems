"""
The local oscillators: TI LMX2582 (and LMX2572) as a phase-noise profile, an
output level, and the spurs on it.

Datasheet SNAS680E, table 6.5:
    open-loop VCO at 5.5 GHz: -89.7 / -114.0 / -134.9 / -151.3 / -153.3 dBc/Hz
    at 10 k / 100 k / 1 M / 10 M / 100 MHz offset; the 900 MHz and 1.8 GHz
    rows are these less 20 log(f) and the divider (within 0.6 dB of the
    datasheet's own rows from 10 kHz to 1 MHz: test_sasim.py), with the
    floors those rows show after a divider (-157.7 and -161.1 dBc/Hz)
    normalised PLL noise floor -231 dBc/Hz, flicker -126 dBc/Hz
    output 8 dBm typical (3 GHz, single-ended, 50 ohm pull-up)
    HD2 -27 dBc, HD3 -25 dBc (5 GHz); phase-detector spur -93 dBc
The loop is a second-order approximation: the PLL's noise inside the loop
bandwidth, the VCO's outside.
"""

from dataclasses import dataclass, field

import numpy as np

_OFF = np.log10([1e4, 1e5, 1e6, 1e7, 1e8])


def _db_sum(*x):
    return 10 * np.log10(sum(10 ** (np.asarray(v, float) / 10) for v in x))


@dataclass
class Synth:
    name: str = "LMX2582"
    source: str = ("TI datasheet SNAS680E table 6.5 (VCO and PLL noise, "
                   "output power, harmonics, PFD spur); loop bandwidth chosen")
    fmin: float = 20e6
    fmax: float = 5.5e9
    vco: tuple = (3.55e9, 7.1e9)
    vco_ref_hz: float = 5.5e9
    vco_pn: tuple = (-89.7, -114.0, -134.9, -151.3, -153.3)
    floor_direct: float = -153.3
    floor_divided: float = -157.7       # after /2 (the 1.8 GHz row)
    floor_divided4: float = -161.1      # after /4 or more (the 900 MHz row)
    pll_floor: float = -231.0
    pll_flicker: float = -126.0
    fpd: float = 10e6               # phase detector: 40 MHz / 4, integer N
    loop_bw: float = 100e3
    out_dbm: float = 8.0            # at the mixer, after the buffer and pads
    harmonics_dbc: tuple = (-27.0, -25.0)       # 2nd, 3rd (datasheet)
    spurs: list = field(default_factory=lambda: [(10e6, -93.0)])
    dividers: tuple = (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 72, 96, 128,
                       192)

    def divider(self, fout):
        for d in self.dividers:
            if self.vco[0] <= fout * d <= self.vco[1]:
                return d
        raise ValueError("%s cannot make %.4f GHz" % (self.name, fout / 1e9))

    def check(self, fout):
        if not self.fmin <= fout <= self.fmax:
            raise ValueError("%s: %.4f GHz is outside %.3g-%.3g GHz"
                             % (self.name, fout / 1e9, self.fmin / 1e9,
                                self.fmax / 1e9))

    def step(self, fout):
        """The integer-N step at this output: fpd / divider."""
        return self.fpd / self.divider(fout)

    def phase_noise(self, fout, offset):
        """L(f), dBc/Hz, of the locked output at these offsets (Hz)."""
        off = np.maximum(np.abs(np.asarray(offset, float)), 1e3)
        lg = np.log10(off)
        d = self.divider(fout)
        vco = np.interp(lg, _OFF, self.vco_pn)
        # -30 dB/decade under 10 kHz (flicker of the VCO), flat past 100 MHz
        vco = np.where(lg < _OFF[0], self.vco_pn[0] - 30 * (lg - _OFF[0]),
                       vco)
        vco = vco + 20 * np.log10(fout * d / self.vco_ref_hz)
        if d > 1:
            # the table has the undivided output's floor in it already; a
            # divider's output has its own
            floor = self.floor_divided if d == 2 else self.floor_divided4
            vco = _db_sum(vco - 20 * np.log10(d), floor)
        flat = (self.pll_floor + 10 * np.log10(self.fpd)
                + 20 * np.log10(fout / self.fpd))
        flick = (self.pll_flicker + 20 * np.log10(fout / 1e9)
                 - 10 * np.log10(off / 1e4))
        pll = _db_sum(flat, flick)
        x = (off / self.loop_bw) ** 4
        return _db_sum(pll - 10 * np.log10(1 + x),
                       vco + 10 * np.log10(x / (1 + x) + 1e-30))


def lmx2582(**kw):
    return Synth(**kw)


def lmx2572(**kw):
    """TI LMX2572, 12.5 MHz-6.4 GHz, VCO 3.2-6.4 GHz.  Datasheet SNAS740:
    -106 dBc/Hz at 100 kHz from a 6.4 GHz carrier, PLL figure of merit -232
    dBc/Hz.  Its VCO profile is taken as the LMX2582's shape, moved to meet
    that one figure (an assumption), and its output as 3 dBm less."""
    d = dict(name="LMX2572",
             source="TI datasheet SNAS740 (-106 dBc/Hz at 100 kHz, 6.4 GHz; "
             "FOM -232); VCO profile shape assumed from the LMX2582's",
             fmin=12.5e6, fmax=6.4e9, vco=(3.2e9, 6.4e9), vco_ref_hz=6.4e9,
             vco_pn=(-81.7, -106.0, -126.9, -146.0, -150.0),
             floor_direct=-150.0, floor_divided=-155.0,
             floor_divided4=-158.0, pll_floor=-232.0,
             pll_flicker=-123.5,
             dividers=(1, 2, 4, 8, 16, 32, 64, 128, 256))
    d.update(kw)
    return Synth(**d)
