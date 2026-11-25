"""
The analyzer: its three signal paths built from the parts, its tuning (where
each LO sits for a wanted input frequency), one capture (4096 ADC samples, an
FFT, and the bins mapped back to input frequency and input power), and a sweep
of captures.

    Band A   to 2.29 GHz with two LMX2582: up-conversion in the first mixer to
             a 3.2 GHz first IF (LO1 = f + IF1, 3.2-5.5 GHz), the printed
             band-pass, the second mixer down to 10 MHz
    Band B1  2.29-3.45 GHz, band B2 3.45-5.49 GHz: straight into the second
             mixer, LO2 10 MHz from the signal: no image rejection in hardware

Image rejection in software: every step can be captured twice, the second
time with the second LO on the other side of the signal (and, in band A, both
LOs moved 10 MHz), and the lower of the two readings kept.  A real response
reads the same both times; an image, and most spurs, do not.
"""

import os
from dataclasses import dataclass

import numpy as np

from . import backend, parts
from .lo import lmx2582
from .parts import GHZ, MHZ
from .rf import KT0, Chain, Lin, Mix, Mixer, State, Tones, dbm, mw


@dataclass
class Signal:
    """A carrier (bw = 0), or a noise-like signal of bandwidth bw (a flat
    density over it: an OFDM or spread-spectrum signal as an analyzer sees
    it).  The noise-like kind goes through the chain as noise does: every
    conversion, image and filter acts on it, but it makes no distortion
    products and does not compress anything, so keep it small."""
    f: float                 # Hz (the centre, when it has a bandwidth)
    dbm: float               # total power
    bw: float = 0.0          # Hz
    duty: float = 1.0        # the chance it is on during a capture
    name: str = ""

    def label(self):
        return self.name or "%.6g MHz" % (self.f / MHZ)


def passband_centre(part, lo=2.5 * GHZ, hi=4.0 * GHZ):
    f = np.arange(lo, hi, 1 * MHZ)
    g = part.g(f)
    inside = f[g > g.max() - 3.0]
    return 0.5 * (inside[0] + inside[-1])


class Analyzer:
    step = 10 * MHZ          # one capture's share of the span
    if2 = 10 * MHZ           # the second IF's centre (a quarter of 40 MSPS)

    def __init__(self, lo1=None, lo2=None, att_db=10.0, if_att_db=6.0,
                 preamp=False, nfft=4096, seed=1, mixer1=None, mixer2=None,
                 adc=None, lna_powered_in_band_a=False, extra_lpf=False,
                 pad1_db=3.0, pad2_db=3.0):
        self.lo1 = lo1 or lmx2582()
        self.lo2 = lo2 or lmx2582()
        self.att_db, self.if_att_db, self.preamp = att_db, if_att_db, preamp
        self.nfft, self.seed = nfft, seed
        self.mx1 = mixer1 or Mixer()
        self.mx2 = mixer2 or Mixer()
        self.adc = adc or parts.Adc()
        self.lna_powered_in_band_a = lna_powered_in_band_a
        self.extra_lpf = extra_lpf
        self.pad1_db, self.pad2_db = pad1_db, pad2_db
        self._build()

    # ------------------------------------------------------------ the design

    def _build(self):
        P = parts
        self.if1_bpf = P.em_filter(
            "IF1 band-pass (hairpin N=5)", "if1_bpf_3g2_hairpin.s2p",
            "3.2 GHz, 180 MHz, RO4003C 0.508 mm")
        self.if1_lpf = P.em_filter(
            "IF1 low-pass (radial stub N=7)", "if1_lpf_4g0_radial.s2p",
            "4.0 GHz, RO4003C 0.508 mm")
        b_bpf = {
            "B1": P.em_filter("band B1 preselector (stub N=5)",
                              "b1_bpf_2g3_3g5_stub.s2p",
                              "2.3-3.5 GHz, RO4003C 0.508 mm"),
            "B2": P.em_filter("band B2 preselector (stub N=5)",
                              "b2_bpf_3g4_5g5_stub.s2p",
                              "3.4-5.5 GHz, RO4003C 0.508 mm")}
        # a stub band-pass passes again above its band (B1's at 5.5 GHz and
        # 8-11 GHz, mom25d finds): each is followed by a low-pass
        b_lpf = {
            "B1": self.if1_lpf,
            "B2": P.em_filter("band B2 low-pass (radial stub N=7)",
                              "b2_lpf_6g4_radial.s2p",
                              "6.4 GHz, RO4003C 0.508 mm")}
        # the first IF sits where this board's filter does
        self.if1 = round(passband_centre(self.if1_bpf) / self.step) * self.step
        self.a_top = min(2.3 * GHZ, np.floor(
            (self.lo1.fmax - self.if1) / self.step) * self.step)
        self.b_split = 3.45 * GHZ
        self.b_top = self.lo2.fmax - self.if2

        front = [Lin(P.dc_block_limiter()),
                 Lin(P.pe43711(self.att_db, "input PE43711"))]
        lna = (P.pma3_83ln("preamp PMA3-83LN+") if self.preamp else
               P.pad(1.6, "preamp bypass (two switch passes)"))
        lna_a = (P.pma3_83ln("preamp PMA3-83LN+")
                 if self.lna_powered_in_band_a else
                 P.amplifier_off("preamp PMA3-83LN+ (unpowered)"))
        if2_out = (0.0, backend.OSR * self.adc.fs / 2)

        a = (front + [Lin(P.pe42540("on", "SW1 (band select)")),
                      Lin(P.lfcn2250()), Lin(P.lfcn2250())]
             + ([Lin(self.if1_lpf)] if self.extra_lpf else [])
             + [Lin(P.pad(self.pad1_db, "%g dB pad (mixer 1 IF port)"
                          % self.pad1_db)),
                Mix(self.mx1, "lo1", "up", out=(0.2 * GHZ, 14 * GHZ)),
                Lin(P.pad(self.pad2_db, "%g dB pad (mixer 1 RF port)"
                          % self.pad2_db)),
                Lin(self.if1_bpf),
                Lin(P.pma3_83ln("IF1 amp PMA3-83LN+")),
                Lin(self.if1_lpf),
                Lin(P.pe42540("on", "SW2 (mixer 2 select)")),
                Mix(self.mx2, "lo2", "down", out=if2_out)])
        leak = [Lin(P.pe42540("off", "SW1")), Lin(b_bpf["B2"]),
                Lin(b_lpf["B2"]),
                Lin(P.pe42540("on", "SW3 (B1/B2)")), Lin(lna_a),
                Lin(P.pe42540("off", "SW2"))]
        self.chains = {"A": Chain("A", a, leak, fork=2, join=len(a) - 1)}
        for b in ("B1", "B2"):
            self.chains[b] = Chain(b, front + [
                Lin(P.pe42540("on", "SW1 (band select)")), Lin(b_bpf[b]),
                Lin(b_lpf[b]),
                Lin(P.pe42540("on", "SW3 (B1/B2)")), Lin(lna),
                Lin(P.pe42540("on", "SW2 (mixer 2 select)")),
                Mix(self.mx2, "lo2", "down", out=if2_out)])
        self.if_parts = [P.diplexer_lp(), P.lmh6629(20.0, "LMH6629 #1"),
                         P.pe43711(self.if_att_db, "IF PE43711"),
                         P.antialias(), P.lmh6629(20.0, "LMH6629 #2"),
                         P.ths4541()]
        self.fs_node_dbm = (self.adc.fs_dbm_at_driver_input
                            + float(self.if_parts[-1].g(self.if2)))
        self.adc_model = backend.AdcModel(self.adc, self.fs_node_dbm)

    def all_parts(self):
        out = []
        for c in self.chains.values():
            for s in c.main + c.leak:
                out.append(s.part if isinstance(s, Lin) else s.mx)
        return out + self.if_parts + [self.lo1, self.lo2, self.adc]

    # ---------------------------------------------------------------- tuning

    def band(self, fc):
        if fc <= self.a_top:
            return "A"
        if fc < self.b_split:
            return "B1"
        if fc <= self.b_top:
            return "B2"
        raise ValueError("%.4f GHz is above the analyzer's %.4f GHz"
                         % (fc / GHZ, self.b_top / GHZ))

    def tune(self, fc, shifted=False):
        """The LOs for a capture centred on fc, and how an IF bin maps to
        an input frequency: f_in = base + sign * f_if."""
        band = self.band(fc)
        if band == "A":
            if not shifted:
                lo1, lo2 = fc + self.if1, self.if1 - self.if2
                base, sign = lo1 - lo2, -1
            else:
                lo1, lo2 = fc + self.if1 - self.step, self.if1
                base, sign = lo1 - lo2, +1
            self.lo1.check(lo1)
        else:
            lo1 = None
            lo2 = fc + self.if2 if not shifted else fc - self.if2
            base, sign = lo2, (-1 if not shifted else +1)
        self.lo2.check(lo2)
        st = State(lo={"lo1": lo1, "lo2": lo2},
                   synth={"lo1": self.lo1, "lo2": self.lo2})
        return band, st, base, sign

    # --------------------------------------------------------------- capture

    def _rng(self, fc, shifted, sweep=0):
        return np.random.default_rng(
            [self.seed, int(round(fc / 1e3)), int(shifted), sweep])

    def rf(self, signals, fc, shifted=False, rng=None):
        """The second mixer's output for a capture at fc: (band, state,
        base, sign, tones, noise function)."""
        rng = rng or self._rng(fc, shifted)
        band, st, base, sign = self.tune(fc, shifted)
        on = [s for s in signals
              if s.duty >= 1.0 or rng.uniform() < s.duty]
        cw = [s for s in on if s.bw <= 0]
        tin = Tones([s.f for s in cw], [s.dbm for s in cw],
                    [s.label() for s in cw])
        wide = [(s.f - s.bw / 2, s.f + s.bw / 2, mw(s.dbm) / s.bw)
                for s in on if s.bw > 0]

        def psd_in(f):
            f = np.asarray(f, float)
            return sum(d * ((f >= a) & (f <= b)) for a, b, d in wide)
        t, noise = self.chains[band].run(tin, st, psd_in if wide else None)
        return band, st, base, sign, t, noise

    def capture(self, signals, fc, shifted=False, sweep=0):
        """One capture centred on fc: (input frequency of each kept bin,
        the power it reads in dBm at the input connector, details)."""
        rng = self._rng(fc, shifted, sweep)
        band, st, base, sign, t, noise = self.rf(signals, fc, shifted, rng)
        n = backend.OSR * (self.nfft + 2 * backend.GUARD)
        fs = backend.OSR * self.adc.fs
        f = np.fft.rfftfreq(n, 1 / fs)
        # the noise density: every bin under 40 MHz (the skirts are sharp),
        # every sixteenth above (the anti-alias filter removes it anyway)
        fine = f <= 40 * MHZ
        idx = np.unique(np.concatenate([np.flatnonzero(fine),
                                        np.arange(0, f.size, 16),
                                        [f.size - 1]]))
        psd = mw(np.interp(f, f[idx], dbm(noise(f[idx]))))
        x = backend.synthesize(t, psd, n, fs, rng)
        x, load = backend.if_chain(x, self.if_parts, fs, rng, f)
        codes, clipped = self.adc_model.convert(x, fs, rng)
        fb, dbfs, enbw = backend.spectrum(codes, self.adc.bits, self.adc.fs)
        fin = base + sign * fb
        keep = (fin >= fc - self.step / 2 - 1) & (fin < fc + self.step / 2 - 1)
        fin, fb, dbfs = fin[keep], fb[keep], dbfs[keep]
        order = np.argsort(fin)
        fin, fb, dbfs = fin[order], fb[order], dbfs[order]
        gain = self.gain_to_adc(band, st, fin, fb)
        info = dict(band=band, lo=st.lo, clipped=clipped, if_load=load,
                    levels=list(st.levels), enbw=enbw, if_tones=t,
                    base=base, sign=sign)
        return fin, dbfs + self.fs_node_dbm - gain, info

    def gain_to_adc(self, band, st, fin, fif):
        """The wanted response's gain, in dB, from the input connector to
        the ADC's input node: what the analyzer's amplitude calibration
        takes out."""
        _, g = self.chains[band].wanted_gain(fin, st)
        return g + sum(p.g(fif) for p in self.if_parts)

    # ----------------------------------------------------------------- sweep

    def centres(self, f0, f1):
        k0 = int(np.floor(f0 / self.step + 0.5))
        k1 = int(np.ceil(f1 / self.step - 0.5))
        return [k * self.step for k in range(max(k0, 1), max(k1, k0) + 1)]

    def _step(self, signals, fc, image_reject, sweep=0):
        f, p, info = self.capture(signals, fc, False, sweep)
        clipped = info["clipped"]
        if image_reject:
            f2, p2, info2 = self.capture(signals, fc, True, sweep)
            p = np.minimum(p, p2)
            clipped = max(clipped, info2["clipped"])
        return f, p, clipped

    def sweep(self, signals, f0, f1, image_reject=True, workers=None,
              sweep=0):
        """A sweep from f0 to f1: (f, dBm, the worst fraction of clipped ADC
        samples in any capture)."""
        cs = self.centres(f0, f1)
        workers = workers or min(os.cpu_count() or 1, 8)
        if workers > 1 and len(cs) > 3:
            import multiprocessing as mp
            global _JOB
            _JOB = (self, signals, image_reject, sweep)
            with mp.get_context("fork").Pool(workers) as pool:
                res = pool.map(_work, cs, chunksize=1)
        else:
            res = [self._step(signals, fc, image_reject, sweep) for fc in cs]
        f = np.concatenate([r[0] for r in res])
        p = np.concatenate([r[1] for r in res])
        keep = (f >= f0) & (f <= f1)
        return f[keep], p[keep], max(r[2] for r in res)

    # ------------------------------------------------------------- analysis

    def explain(self, signals, f, shifted=False, top=6, span=None):
        """What makes the reading at input frequency f, strongest first,
        as (displayed frequency, dBm referred to the input, what).  Two
        kinds: the products at the second mixer's output that land within
        `span` of f, and (with the level None: the time-domain half makes
        them, and only a capture gives their size) the harmonics that the
        IF amplifiers and the ADC make of a strong IF tone, aliased."""
        fc = self.centres(f, f)[0]
        band, st, base, sign, t, _ = self.rf(signals, fc, shifted)
        span = span or 2 * self.adc.fs / self.nfft
        out = []
        if len(t):
            fdisp = base + sign * t.f
            near = np.flatnonzero(np.abs(fdisp - f) <= span)
            if near.size:
                g = self.gain_to_adc(band, st, fdisp[near], t.f[near])
                gif = sum(p.g(t.f[near]) for p in self.if_parts)
                lev = t.p[near] + gif - g
                for i in np.argsort(-lev)[:top]:
                    out.append((float(fdisp[near][i]), float(lev[i]),
                                str(t.tag[near][i])))
            fs = self.adc.fs
            at_adc = t.p + sum(p.g(t.f) for p in self.if_parts)
            # a tone past the ADC's Nyquist frequency, through what the
            # anti-alias filter leaves of it
            fal = t.f % fs
            fal = np.minimum(fal, fs - fal)
            fda = base + sign * fal
            for i in np.flatnonzero((t.f > fs / 2)
                                    & (np.abs(fda - f) <= span)):
                g = self.gain_to_adc(band, st, fda[i:i + 1], fal[i:i + 1])
                out.append((float(fda[i]), float(at_adc[i] - g[0]),
                            "alias of [%s] at an IF of %.1f MHz, through "
                            "the anti-alias filter" % (t.tag[i],
                                                       t.f[i] / MHZ)))
            out.sort(key=lambda x: -x[1])
            for i in np.argsort(-at_adc)[:4]:
                if at_adc[i] < self.fs_node_dbm - 50 or t.f[i] > fs / 2:
                    continue
                for k in (2, 3):
                    fa = (k * t.f[i]) % fs
                    fa = min(fa, fs - fa)
                    if abs(base + sign * fa - f) <= span:
                        out.append((float(base + sign * fa), None,
                                    "harmonic %d of [%s] in the IF "
                                    "amplifiers and ADC" % (k, t.tag[i])))
        return out

    def budget(self, fin, shifted=False):
        """The wanted path at fin, stage by stage, and the system's figures
        there.  Returns (rows, summary)."""
        fc = self.centres(fin, fin)[0]
        band, st, base, sign = self.tune(fc, shifted)
        chain = self.chains[band]
        chain.run(Tones(), st)          # the LO leaks, for the noise pass
        rows, f = [], float(fin)
        g_cum, f_cum, ip3_inv, p1 = 0.0, 1.0, 0.0, 1e9
        stages = list(chain.main) + [Lin(p) for p in self.if_parts]
        for s in stages:
            if isinstance(s, Lin):
                g = float(s.part.g(f))
                nf = float(s.part.noise_figure(f))
                ip3, ip1 = s.part.in_ip3(f), s.part.in_p1db(f)
                fo = f
            else:
                fo, g = s.wanted(f, st)
                fo, g = float(fo), float(g)
                nf, ip3, ip1 = -g, s.mx.iip3, s.mx.ip1db
            f_cum += (mw(nf) - 1) / mw(g_cum)
            ip3_inv += mw(g_cum) / mw(ip3)
            p1 = min(p1, ip1 - g_cum)
            g_cum += g
            rows.append(dict(stage=s.name, f=f, gain=g, nf=nf, iip3=ip3,
                             cum_gain=g_cum, cum_nf=float(dbm(f_cum)),
                             cum_iip3=float(-dbm(ip3_inv))))
            f = fo
        # the noise that really arrives (images and LO noise included)
        _, noise = chain.run(Tones(), st)
        fif = abs(fin - base)
        n = float(noise(np.array([fif]))[0])
        for p in self.if_parts:
            g = mw(p.g(fif))
            n = (g * n + max(1 - g, 0) * KT0 if p.nf is None else
                 g * (n + (mw(p.noise_figure(fif)) - 1) * KT0))
        n_adc = mw(self.fs_node_dbm - 0.5 - self.adc.snr_db
                   - 10 * np.log10(self.adc.fs / 2))
        gain = float(self.gain_to_adc(band, st, np.array([fin]),
                                      np.array([fif]))[0])
        danl = float(dbm(n + n_adc)) - gain
        iip3 = float(-dbm(ip3_inv))
        summary = dict(
            band=band, f=fin, lo1=st.lo["lo1"], lo2=st.lo["lo2"],
            gain_to_adc=gain, nf_friis=float(dbm(f_cum)),
            danl_dbm_hz=danl, nf_effective=danl + 174.0,
            chain_noise_over_adc_db=float(dbm(n) - dbm(n_adc)),
            iip3=iip3, ip1db=p1, adc_full_scale_dbm=self.fs_node_dbm - gain,
            sfdr_db_1hz=2.0 / 3.0 * (iip3 - danl))
        return rows, summary


_JOB = None


def _work(fc):
    sa, signals, image_reject, sweep = _JOB
    return sa._step(signals, fc, image_reject, sweep)
