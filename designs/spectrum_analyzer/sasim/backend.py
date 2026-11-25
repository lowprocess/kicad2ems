"""
The 10 MHz half of the analyzer, in the time domain: what leaves the second
mixer (tones, and a noise density) is made into a waveform at eight times the
ADC's rate, taken through the IF amplifiers and filters (their gain against
frequency, their noise, and their distortion as a cubic applied to the
waveform, so every product of every signal is there), sampled with the ADC's
aperture jitter, and converted with its noise, its INL and its clipping.

The waveform's unit is the square root of a milliwatt: mean(x^2) is the power
in mW, so a tone of P dBm has the peak amplitude sqrt(2 * 10^(P/10)).
"""

import numpy as np

from .parts import INF
from .rf import KT0, mw

OSR = 8          # waveform rate / ADC rate
GUARD = 256      # ADC samples dropped at each end (the filters' ringing)


def _noise(rng, psd, n, fs):
    """A waveform of n samples whose one-sided density is psd (mW/Hz at
    rfftfreq(n, 1/fs))."""
    k = psd.size
    x = (rng.standard_normal(k) + 1j * rng.standard_normal(k)) / np.sqrt(2)
    x *= np.sqrt(psd * (fs / n) / 2) * n
    x[0] = 0
    return np.fft.irfft(x, n)


def _filter(x, g_db):
    return np.fft.irfft(np.fft.rfft(x) * 10 ** (g_db / 20), x.size)


def synthesize(tones, noise_psd, n, fs, rng, floor_dbm=-150.0,
               max_tones=600):
    """Tones (each at a random phase) and noise, as a waveform."""
    t = np.arange(n) / fs
    x = _noise(rng, noise_psd, n, fs)
    keep = np.flatnonzero((tones.p > floor_dbm) & (tones.f < fs / 2))
    keep = keep[np.argsort(-tones.p[keep])][:max_tones]
    amp = np.sqrt(2 * mw(tones.p[keep]))
    ph = rng.uniform(0, 2 * np.pi, keep.size)
    for i in range(0, keep.size, 16):
        j = keep[i:i + 16]
        x += (amp[i:i + 16, None] * np.cos(
            2 * np.pi * tones.f[j, None] * t[None, :]
            + ph[i:i + 16, None])).sum(0)
    return x


def if_chain(x, parts, fs, rng, f):
    """The waveform through the IF parts, in order.  Returns it and each
    stage's peak, as a fraction of where that stage clips."""
    load = []
    for p in parts:
        g = p.g(f)
        if p.nf is None:
            # passive: its loss, and that loss's thermal noise
            x = _filter(x, g)
            x += _noise(rng, np.maximum(1 - mw(g), 0) * KT0, x.size, fs)
            continue
        x += _noise(rng, (mw(p.noise_figure(f)) - 1) * KT0, x.size, fs)
        x = _filter(x, g)
        y = x
        if p.oip2 < INF:
            y = y + x * x / np.sqrt(2 * mw(p.oip2))
        if p.oip3 < INF:
            y = y - x * x * x * 2 / (3 * mw(p.oip3))
        sat = np.sqrt(2 * mw(p.op1db + 2.0)) if p.op1db < INF else INF
        load.append((p.name, float(np.max(np.abs(x)) / sat)))
        x = np.clip(y, -sat, sat)
    return x, load


class AdcModel:
    def __init__(self, adc, fs_dbm, seed=12040):
        """fs_dbm: the power at the ADC's input node of a full-scale sine."""
        self.adc = adc
        self.a_fs = np.sqrt(2 * mw(fs_dbm))
        self.levels = 2 ** adc.bits
        self.half = self.levels // 2
        q = 2.0 / self.levels
        total = (10 ** (-0.5 / 20) / np.sqrt(2)) * 10 ** (-adc.snr_db / 20)
        self.sigma = np.sqrt(max(total ** 2 - q * q / 12, 0.0))
        # a fixed INL for this converter: three slow sinusoids and a random
        # walk (the DNL), scaled to the datasheet's typical sizes
        r = np.random.default_rng(seed)
        u = np.arange(self.levels) / self.levels
        slow = sum(a * np.sin(2 * np.pi * (c * u + r.uniform()))
                   for a, c in ((0.5, 1.0), (0.3, 2.3), (0.2, 5.1)))
        walk = np.cumsum(r.uniform(-1, 1, self.levels) * adc.dnl_lsb / 4)
        walk -= np.linspace(walk[0], walk[-1], self.levels)
        inl = slow + walk
        self.inl = inl * adc.inl_lsb / np.max(np.abs(inl))
        # the smooth part of its distortion, as HD2 and HD3 at full scale
        self.c2 = 2 * 10 ** (adc.hd2_dbc / 20)
        self.c3 = 4 * 10 ** (adc.hd3_dbc / 20)

    def convert(self, x, fs, rng):
        """The codes (signed) of the waveform x at rate fs = OSR * ADC rate,
        with the guard samples dropped."""
        a = self.adc
        dxdt = np.gradient(x) * fs
        idx = np.arange(GUARD * OSR, x.size - GUARD * OSR, OSR)
        jit = np.hypot(a.jitter_s, a.clock_jitter_s)
        v = (x[idx] + dxdt[idx] * rng.standard_normal(idx.size) * jit)
        v = v / self.a_fs
        v = v + self.c2 * v * v + self.c3 * v ** 3
        v = v + rng.standard_normal(idx.size) * self.sigma
        lsb = v * self.half
        code = np.clip(np.floor(lsb), -self.half, self.half - 1).astype(int)
        lsb = lsb + self.inl[code + self.half]
        code = np.clip(np.round(lsb), -self.half, self.half - 1)
        clipped = float(np.mean(np.abs(code) >= self.half - 1))
        return code.astype(int), clipped


def spectrum(codes, bits, fs, pad=2):
    """(f, dBFS per bin, the window's noise bandwidth in Hz): a 4-term
    Blackman-Harris FFT of the codes, zero-padded to `pad` times their
    length (so a tone between two bins reads within 0.2 dB); a full-scale
    sine reads 0 dBFS."""
    n = codes.size
    k = np.arange(n)
    w = (0.35875 - 0.48829 * np.cos(2 * np.pi * k / n)
         + 0.14128 * np.cos(4 * np.pi * k / n)
         - 0.01168 * np.cos(6 * np.pi * k / n))
    v = codes / 2.0 ** (bits - 1)
    s = np.fft.rfft((v - v.mean()) * w, pad * n) * 2 / w.sum()
    enbw = fs * (w * w).sum() / w.sum() ** 2
    return (np.fft.rfftfreq(pad * n, 1 / fs),
            20 * np.log10(np.maximum(np.abs(s), 1e-12)), enbw)
