#!/usr/bin/env python3
"""
Run the spectrum analyzer's simulation: its budget, and the test cases that
show what it does with real signals.

    python run.py                  every case, figures and report.md in out/
    python run.py budget           the gain / noise / linearity budget only
    python run.py cw im3           chosen cases
    python run.py --list

The cases:
    budget        stage-by-stage gain, noise figure and IP3 in each band
    cw            a carrier at 1 GHz: its image, its skirts, the noise floor
    im3           two tones 1 MHz apart: third-order products against the
                  input attenuator
    compression   reading against input level, to the ADC's clipping
    feedthrough   a strong signal on the first IF, seen from band A
    residuals     band A with nothing connected: the analyzer's own spurs
    bandb         band B: a carrier and its image, and a bursting 18 MHz
                  wide signal, which two captures show only in pieces
    full          10 MHz to 5.49 GHz with five signals
    strong        one 0 dBm signal: every product the analyzer makes of it

Options: --att DB (input attenuator), --if-att DB, --preamp, --seed N,
--out DIR, --lo2 lmx2572 (the 6.4 GHz part: band B to 6.39 GHz), --pads DB DB
(the pads either side of the first mixer), --extra-lpf.
"""

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sasim import Analyzer, Signal, lo, parts  # noqa: E402

GHZ, MHZ = 1e9, 1e6
# the series colours: a validated categorical order (blue, orange, aqua)
C1, C2, C3 = "#257de0", "#f47230", "#00b894"
INK, INK2, GRID, AXIS = "#191919", "#666666", "#e7e7e7", "#c2c2c2"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 130, "savefig.dpi": 130, "font.size": 9,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
        "axes.titlecolor": INK, "axes.titlesize": 10,
        "axes.titleweight": "bold", "axes.titlelocation": "left",
        "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
        "legend.frameon": False, "lines.linewidth": 1.0})
    return plt


def peak_trace(f, p, points=2400):
    """The trace as a display draws it: the peak of each bucket."""
    if f.size <= points:
        return f, p
    n = f.size // points
    m = (f.size // n) * n
    return (f[:m].reshape(-1, n).mean(1), p[:m].reshape(-1, n).max(1))


def reading(f, p, at, span=40e3):
    near = np.abs(f - at) <= span
    return float(p[near].max()) if near.any() else float("nan")


def level(f, p, at, span=40e3):
    """The mean power around `at`: what a noise marker reads."""
    near = np.abs(f - at) <= span
    return float(10 * np.log10(np.mean(10 ** (p[near] / 10))))


def floor_dbm(p):
    """The noise floor of a trace: its median, as power."""
    return float(np.median(p))


def why(sa, signals, f, reads, shifted=False):
    """The likeliest maker of the reading `reads` at f, as text."""
    found = sa.explain(signals, f, shifted, span=30e3)
    rf = [x for x in found if x[1] is not None]
    ifh = [x for x in found if x[1] is None]
    if rf and rf[0][1] > reads - 8.0:
        return "`%s`" % rf[0][2]
    if ifh:
        return ifh[0][2]
    if rf:
        return "`%s` (%.0f dBm of it)" % (rf[0][2], rf[0][1])
    wide = [x for x in sa.explain(signals, f, shifted, span=400e3)
            if x[1] is not None]
    return "the skirt of `%s`" % wide[0][2] if wide else "-"


class Report:
    def __init__(self, out):
        self.out, self.lines = out, []
        os.makedirs(out, exist_ok=True)

    def say(self, text=""):
        print(text)
        self.lines.append(text)

    def table(self, head, rows):
        self.say("| " + " | ".join(head) + " |")
        self.say("|" + "|".join("---" for _ in head) + "|")
        for r in rows:
            self.say("| " + " | ".join(str(c) for c in r) + " |")
        self.say()

    def figure(self, fig, name, caption):
        path = os.path.join(self.out, name)
        fig.savefig(path, bbox_inches="tight")
        import matplotlib.pyplot as plt
        plt.close(fig)
        self.say("![%s](%s)" % (caption, name))
        self.say()

    def save(self):
        with open(os.path.join(self.out, "report.md"), "w") as fh:
            fh.write("\n".join(self.lines) + "\n")


def spectrum_figure(traces, title, note=None, marks=(), ylim=None):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    span = max(t[0][-1] for t in traces) - min(t[0][0] for t in traces)
    unit, scale = ("GHz", GHZ) if span > 1.5 * GHZ else ("MHz", MHZ)
    for k, ((f, p, label), c) in enumerate(zip(traces, (C2, C1, C3))):
        fd, pd = peak_trace(f, p)
        ax.plot(fd / scale, pd, color=c, label=label, zorder=5 - k)
    for fm, pm, text in marks:
        ax.annotate(text, (fm / scale, pm), xytext=(0, 9),
                    textcoords="offset points", ha="center", fontsize=8,
                    color=INK, arrowprops=dict(arrowstyle="-", color=AXIS,
                                               lw=0.6))
    ax.set_xlabel("input frequency, %s" % unit)
    ax.set_ylabel("reading, dBm")
    ax.set_title(title)
    lo_, hi_ = ax.get_ylim()
    ax.set_ylim(*(ylim or (lo_, hi_ + (12 if marks else 0))))
    if len(traces) > 1:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16),
                  ncol=len(traces))
    if note:
        fig.text(0.125, -0.02, note, fontsize=8, color=INK2, va="top")
    return fig


# --------------------------------------------------------------------- cases

def case_budget(sa, rep, a):
    rep.say("## Budget")
    rep.say()
    rep.say("Input attenuator %g dB, IF attenuator %g dB, preamp %s. The "
            "first IF is %.3f GHz (this filter's centre); band A ends at "
            "%.2f GHz." % (sa.att_db, sa.if_att_db,
                           "on" if sa.preamp else "off", sa.if1 / GHZ,
                           sa.a_top / GHZ))
    rep.say()
    rows = []
    for f in (0.1, 0.5, 1.0, 1.5, 2.0, 2.29, 2.5, 3.0, 3.4, 4.0, 5.0, 5.49,
              6.0, 6.39):
        if f * GHZ > sa.b_top:
            continue
        _, s = sa.budget(f * GHZ)
        rows.append(("%.2f" % f, s["band"], "%.1f" % s["gain_to_adc"],
                     "%.1f" % s["nf_friis"], "%.1f" % s["nf_effective"],
                     "%.1f" % s["danl_dbm_hz"], "%+.1f" % s["iip3"],
                     "%+.1f" % s["ip1db"],
                     "%+.1f" % s["adc_full_scale_dbm"],
                     "%.0f" % s["sfdr_db_1hz"]))
    rep.table(["f, GHz", "band", "gain to ADC, dB", "NF (Friis), dB",
               "NF (effective), dB", "noise floor, dBm/Hz", "IIP3, dBm",
               "P1dB in, dBm", "ADC full scale, dBm", "SFDR, dB (1 Hz)"],
              rows)
    rows = []
    for f, pre in ((0.1, False), (0.1, True), (1.0, False), (1.0, True),
                   (2.0, True), (2.5, False), (2.5, True), (4.0, False),
                   (4.0, True), (5.4, True)):
        if f * GHZ > sa.b_top:
            continue
        s0 = Analyzer(lo1=sa.lo1, lo2=sa.lo2, att_db=0.0, preamp=pre,
                      if_att_db=sa.if_att_db,
                      extra_lpf=sa.extra_lpf, pad1_db=sa.pad1_db,
                      pad2_db=sa.pad2_db)
        _, s = s0.budget(f * GHZ)
        rows.append(("%.1f" % f, s["band"], "on" if pre else "off",
                     "%.1f" % s["nf_effective"],
                     "%.1f" % s["danl_dbm_hz"], "%+.1f" % s["iip3"],
                     "%+.1f" % s["adc_full_scale_dbm"],
                     "%.0f" % s["sfdr_db_1hz"]))
    rep.say("With the input attenuator at 0 dB (the most sensitive "
            "setting), without and with the preamps (a TRF37D73 in band A, "
            "a PMA3-83LN+ in band B, each after its band's first filter):")
    rep.say()
    rep.table(["f, GHz", "band", "preamp", "NF (effective), dB",
               "noise floor, dBm/Hz", "IIP3, dBm", "ADC full scale, dBm",
               "SFDR, dB (1 Hz)"], rows)
    rep.say("The effective noise figure is what the noise pass finds: it has "
            "the second mixer's image, the LOs' noise and the ADC's, which "
            "the Friis column leaves out. SFDR is 2/3 (IIP3 - noise floor) "
            "in 1 Hz; take 2/3 of 10 log(RBW) off for a wider one.")
    rep.say()
    for f in (1.0 * GHZ, 4.0 * GHZ):
        rws, s = sa.budget(f)
        if f > sa.b_top:
            continue
        rep.say("### Stage by stage at %.1f GHz (band %s)" % (f / GHZ,
                                                             s["band"]))
        rep.say()
        rep.table(["stage", "at, MHz", "gain, dB", "NF, dB", "IIP3, dBm",
                   "gain so far", "NF so far", "IIP3 so far"],
                  [(r["stage"], "%.1f" % (r["f"] / MHZ), "%+.1f" % r["gain"],
                    "%.1f" % r["nf"],
                    "-" if r["iip3"] > 200 else "%+.0f" % r["iip3"],
                    "%+.1f" % r["cum_gain"], "%.1f" % r["cum_nf"],
                    "%+.1f" % r["cum_iip3"]) for r in rws])


def case_cw(sa, rep, a):
    rep.say("## A carrier at 1 GHz, -30 dBm")
    rep.say()
    sig = [Signal(1.0 * GHZ, -30.0)]
    f0, f1 = 0.9 * GHZ, 1.1 * GHZ
    f, p1, _ = sa.sweep(sig, f0, f1, image_reject=False)
    _, p2, _ = sa.sweep(sig, f0, f1, image_reject=True)
    rbw = sa.capture([], 1.0 * GHZ)[2]["enbw"]
    img = 1.0 * GHZ - 2 * sa.if2
    rows = [("carrier", "%.1f" % reading(f, p1, 1.0 * GHZ),
             "%.1f" % reading(f, p2, 1.0 * GHZ)),
            ("image, 20 MHz below", "%.1f" % reading(f, p1, img, 6e6),
             "%.1f" % reading(f, p2, img, 6e6)),
            ("noise floor (median)", "%.1f" % floor_dbm(p1),
             "%.1f" % floor_dbm(p2))]
    for off in (100e3, 300e3):
        n1 = level(f, p1, 1.0 * GHZ + off, 15e3)
        n2 = level(f, p2, 1.0 * GHZ + off, 15e3)
        rows.append(("skirt at +%g kHz (mean)" % (off / 1e3), "%.1f" % n1,
                     "%.1f" % n2))
    rep.table(["reading, dBm (RBW %.1f kHz)" % (rbw / 1e3),
               "one capture per step", "two captures, the lower kept"], rows)
    lo1 = sa.lo1.phase_noise(1.0 * GHZ + sa.if1, 100e3)
    lo2 = sa.lo2.phase_noise(sa.if1 - sa.if2, 100e3)
    rep.say("At 100 kHz the two LOs' phase noise is %.1f and %.1f dBc/Hz "
            "(LMX2582 datasheet profile at %.2f and %.2f GHz), which in this "
            "RBW is %.1f dBc: the skirt above."
            % (lo1, lo2, (1.0 * GHZ + sa.if1) / GHZ,
               (sa.if1 - sa.if2) / GHZ,
               10 * np.log10(10 ** (lo1 / 10) + 10 ** (lo2 / 10))
               + 10 * np.log10(rbw)))
    rep.say()
    rep.say("The lower of two readings of noise is 3 dB under either: with "
            "two captures the floor, and any noise-like signal, reads 3 dB "
            "low.")
    rep.say()
    fig = spectrum_figure(
        [(f, p1, "one capture per step"),
         (f, p2, "two captures, the lower kept")],
        "A -30 dBm carrier at 1 GHz, band A",
        marks=[(img, reading(f, p1, img, 6e6), "image of the second mixer")])
    rep.figure(fig, "cw.png", "A carrier at 1 GHz")

    # close in, with a 64 times longer capture, against the datasheet
    fine = Analyzer(lo1=sa.lo1, lo2=sa.lo2, att_db=sa.att_db,
                    if_att_db=sa.if_att_db, seed=sa.seed, nfft=262144,
                    preamp=sa.preamp)
    fc, pc, info = fine.capture([Signal(1000.2 * MHZ, -15.0)], 1.0 * GHZ)
    off = fc - 1000.2 * MHZ
    car = reading(fc, pc, 1000.2 * MHZ, 2e3)
    dbc = pc - car - 10 * np.log10(info["enbw"])
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    sel = (off > 2e3) & (off < 4e6)
    edges = np.logspace(np.log10(2e3), np.log10(4e6), 60)
    mid, val = [], []
    for e0, e1 in zip(edges[:-1], edges[1:]):
        m = sel & (off >= e0) & (off < e1)
        if m.any():
            mid.append(np.sqrt(e0 * e1))
            val.append(10 * np.log10(np.mean(10 ** (dbc[m] / 10))))
    o = np.logspace(np.log10(2e3), np.log10(4e6), 200)
    model = 10 * np.log10(
        10 ** (sa.lo1.phase_noise(1.0 * GHZ + sa.if1, o) / 10)
        + 10 ** (sa.lo2.phase_noise(sa.if1 - sa.if2, o) / 10))
    ax.semilogx(o, model, color=C2, lw=1.6,
                label="LO1 + LO2, datasheet profile")
    ax.semilogx(mid, val, color=C1, lw=0, marker="o", ms=3.5,
                label="simulated reading")
    ax.set_xlabel("offset from the carrier, Hz")
    ax.set_ylabel("dBc/Hz")
    ax.set_title("Phase noise on a -15 dBm carrier at 1 GHz")
    ax.legend(loc="upper right")
    rep.say("Close in (a 262144-sample capture, RBW %.0f Hz), the reading "
            "follows the two LOs' datasheet profiles until the analyzer's "
            "noise floor takes over:" % info["enbw"])
    rep.say()
    rep.figure(fig, "cw_phase_noise.png", "Phase noise at 1 GHz")


def case_im3(sa, rep, a):
    lv = -26.0
    rep.say("## Two tones, 1 MHz apart, %g dBm each" % lv)
    rep.say()
    f1, f2 = 1000.2 * MHZ, 1001.2 * MHZ
    rows, traces = [], []
    for att in (0.0, 10.0, 20.0):
        s2 = Analyzer(lo1=sa.lo1, lo2=sa.lo2, att_db=att, seed=sa.seed,
                      if_att_db=sa.if_att_db, nfft=262144)
        f, p, clip = s2.sweep([Signal(f1, lv), Signal(f2, lv)],
                              996 * MHZ, 1006 * MHZ, image_reject=False,
                              workers=1)
        im = max(reading(f, p, 2 * f1 - f2, 1e3),
                 reading(f, p, 2 * f2 - f1, 1e3))
        tone = reading(f, p, f1, 1e3)
        _, s = s2.budget(1.0 * GHZ)
        rows.append(("%g" % att, "%.1f" % tone, "%.1f" % im,
                     "%.1f" % (im - tone), "%.1f" % (2 * (lv - s["iip3"])),
                     "%.1f" % (lv - s["adc_full_scale_dbm"]),
                     "%.1f" % floor_dbm(p)))
        traces.append((f, p, "%g dB" % att))
    rep.table(["input attenuator, dB", "tone reads, dBm", "IM3 reads, dBm",
               "IM3, dBc", "from the budget's IIP3, dBc",
               "each tone at the ADC, dBFS", "floor, dBm"], rows)
    rep.say("With no attenuation the product is the chain's (the two "
            "mixers, mostly), within 2 dB of the budget, whose cascade "
            "adds every stage's product in phase. Each 10 dB of attenuation "
            "should take it down 20 dB; what is left when it does not is "
            "the ADC's own INL, which stays near -100 dBFS whatever the "
            "level.")
    rep.say()
    fig = spectrum_figure(traces, "Two %g dBm tones 1 MHz apart, by input "
                          "attenuator (RBW 0.3 kHz)" % lv)
    rep.figure(fig, "im3.png", "Two-tone intermodulation")


def case_compression(sa, rep, a):
    rep.say("## Reading against input level (1 GHz, attenuator %g dB)"
            % sa.att_db)
    rep.say()
    levels = np.arange(-50.0, 12.1, 2.0)
    out, clip, loads = [], [], []
    for lv in levels:
        f, p, info = sa.capture([Signal(1000.2 * MHZ, lv)], 1.0 * GHZ)
        out.append(reading(f, p, 1000.2 * MHZ))
        clip.append(info["clipped"])
        loads.append(dict(info["levels"]))
    out = np.array(out)
    err = out - levels
    first_clip = next((lv for lv, c in zip(levels, clip) if c > 0), None)
    comp = next((lv for lv, e in zip(levels, err) if e < -1.0), None)
    _, s = sa.budget(1.0 * GHZ)
    rep.say("The ADC's full scale is %+.1f dBm at the input (budget); the "
            "first clipped sample appears at %s dBm, and the reading is 1 dB "
            "low from %s dBm. The first stage to compress (the budget's "
            "cascade) does so at %+.1f dBm at the input."
            % (s["adc_full_scale_dbm"],
               "%+.0f" % first_clip if first_clip is not None else "beyond +12",
               "%+.0f" % comp if comp is not None else "beyond +12",
               s["ip1db"]))
    rep.say()
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.plot(levels, err, color=C1, lw=1.6, marker="o", ms=3)
    ax.axhline(-1.0, color=AXIS, lw=0.8)
    ax.annotate("-1 dB", (levels[0], -1.0), xytext=(2, 3),
                textcoords="offset points", fontsize=8, color=INK2)
    ax.set_xlabel("input level, dBm")
    ax.set_ylabel("reading - input, dB")
    ax.set_ylim(-12, 2)
    ax.set_title("Compression of a 1 GHz carrier (ADC clipping and the "
                 "mixers)")
    rep.figure(fig, "compression.png", "Compression")


def case_feedthrough(sa, rep, a):
    rep.say("## A 0 dBm signal on the first IF (%.2f GHz), seen from band A"
            % (sa.if1 / GHZ))
    rep.say()
    sig = [Signal(sa.if1 + 1.3 * MHZ, 0.0, name="IF1 signal")]
    f, p, clip = sa.sweep(sig, 0.5 * GHZ, 0.7 * GHZ, image_reject=False)
    f, p2, _ = sa.sweep(sig, 0.5 * GHZ, 0.7 * GHZ, image_reject=True)
    worst = float(p.max())
    fw = float(f[np.argmax(p)])
    made = why(sa, sig, fw, worst)
    rep.say("With one capture per step it reads up to %.1f dBm (%.0f dB "
            "under the signal), at every tuning: here at %.2f MHz, made by "
            "%s. With two captures, %.1f dBm. The rejection is the two "
            "LFCN-2250+ (their datasheet limit, 30 dB each at this "
            "frequency), the mixer's own isolation, and the leak around "
            "the band switches."
            % (worst, -worst, fw / MHZ, made, float(p2.max())))
    rep.say()
    fig = spectrum_figure(
        [(f, p, "one capture per step"),
         (f, p2, "two captures, the lower kept")],
        "A 0 dBm signal at %.4f GHz while band A sweeps 500-700 MHz"
        % ((sa.if1 + 1.3 * MHZ) / GHZ))
    rep.figure(fig, "feedthrough.png", "IF feedthrough")


def case_residuals(sa, rep, a):
    rep.say("## Band A with nothing connected")
    rep.say()
    f0, f1 = 20 * MHZ, sa.a_top
    f, p, _ = sa.sweep([], f0, f1, image_reject=False)
    _, p2, _ = sa.sweep([], f0, f1, image_reject=True)
    fl = floor_dbm(p)
    rows = []
    for fs, ps in sorted(spurs(sa, [], f, p), key=lambda x: -x[1])[:25]:
        rows.append(("%.2f" % (fs / MHZ), "%.1f" % ps,
                     "%.1f" % reading(f, p2, fs, 15e3),
                     why(sa, [], fs, ps)))
    rep.say("Noise floor %.1f dBm (median). Responses 15 dB or more over "
            "their step's floor:" % fl)
    rep.say()
    if rows:
        rep.table(["at, MHz", "one capture, dBm", "two captures, dBm",
                   "made by"], rows)
    else:
        rep.say("none.")
        rep.say()
    fig = spectrum_figure(
        [(f, p, "one capture per step"),
         (f, p2, "two captures, the lower kept")],
        "Band A, input terminated: the analyzer's own responses")
    rep.figure(fig, "residuals.png", "Residual responses")


def case_bandb(sa, rep, a):
    rep.say("## Band B: a carrier at 4 GHz, and a bursting 18 MHz wide signal "
            "at 2.442 GHz")
    rep.say()
    cw = [Signal(4.0 * GHZ, -40.0)]
    f, p, _ = sa.sweep(cw, 3.9 * GHZ, 4.1 * GHZ, image_reject=False)
    _, p2, _ = sa.sweep(cw, 3.9 * GHZ, 4.1 * GHZ, image_reject=True)
    img = 4.0 * GHZ - 2 * sa.if2
    rep.table(["reading, dBm", "one capture per step",
               "two captures, the lower kept"],
              [("carrier, 4 GHz", "%.1f" % reading(f, p, 4.0 * GHZ),
                "%.1f" % reading(f, p2, 4.0 * GHZ)),
               ("image, 20 MHz below", "%.1f" % reading(f, p, img, 6e6),
                "%.1f" % reading(f, p2, img, 6e6)),
               ("noise floor", "%.1f" % floor_dbm(p), "%.1f" % floor_dbm(p2))])
    fig = spectrum_figure(
        [(f, p, "one capture per step"),
         (f, p2, "two captures, the lower kept")],
        "A -40 dBm carrier at 4 GHz, band B2 (no image filter)",
        marks=[(img, reading(f, p, img, 6e6), "image")])
    rep.figure(fig, "bandb_cw.png", "Band B carrier")

    bw, lv = 18 * MHZ, -40.0
    burst = [Signal(2.442 * GHZ, lv, bw=bw, duty=0.35, name="burst")]
    steady = [Signal(2.442 * GHZ, lv, bw=bw, name="steady")]
    f0, f1 = 2.38 * GHZ, 2.50 * GHZ
    inrbw = lv - 10 * np.log10(bw / sa.capture([], 2.44 * GHZ)[2]["enbw"])
    traces, rows = [], []
    for ir, label in ((False, "one capture per step"),
                      (True, "two captures, the lower kept")):
        acc = None
        for k in range(8):                      # eight sweeps, max hold
            f, p, _ = sa.sweep(burst, f0, f1, image_reject=ir, sweep=k)
            acc = p if acc is None else np.maximum(acc, p)
        traces.append((f, acc, label))
        _, ps, _ = sa.sweep(steady, f0, f1, image_reject=ir)
        own = np.abs(f - 2.442 * GHZ) < 8 * MHZ
        ghost = np.abs(f - (2.442 * GHZ - 2 * sa.if2)) < 6 * MHZ
        rows.append((label, "%.1f" % level(f, ps, 2.442 * GHZ, 8 * MHZ),
                     "%.1f" % np.median(acc[own]),
                     "%.0f %%" % (100 * np.mean(acc[own] > inrbw - 10)),
                     "%.1f" % np.median(acc[ghost])))
    rep.say("A noise-like signal %g MHz wide, %g dBm in total, is %.1f dBm "
            "in this RBW. Steady, and then on for 35 %% of the captures "
            "(each capture is 0.1 ms), max hold over eight sweeps:"
            % (bw / MHZ, lv, inrbw))
    rep.say()
    rep.table(["", "steady: reads, dBm", "bursting: reads (median), dBm",
               "bursting: share of its band shown", "20 MHz below: reads, "
               "dBm"], rows)
    rep.say("One capture per step shows the signal and its image side by "
            "side. Keeping the lower of two captures removes the image, but "
            "a step shows a bursting signal only when both of its captures "
            "caught it: the signal is shown in pieces, and low.")
    rep.say()
    fig = spectrum_figure(traces, "A bursting 18 MHz wide signal at 2.442 "
                          "GHz, band B1, max hold of 8 sweeps")
    rep.figure(fig, "bandb_burst.png", "Band B burst")


def spurs(sa, signals, f, p, over=15.0, guard=3 * MHZ):
    """The readings that stand `over` dB above their 10 MHz step's median
    and are not within `guard` of a signal: (f, dBm) of each step's peak."""
    n = int(round(sa.step / (f[1] - f[0])))
    out = []
    for i in range(0, f.size - n + 1, n):
        sf, sp = f[i:i + n], p[i:i + n].copy()
        med = np.median(sp)
        for s in signals:
            sp[np.abs(sf - s.f) < guard + s.bw / 2] = -999.0
        j = int(np.argmax(sp))
        if sp[j] > med + over:
            out.append((float(sf[j]), float(sp[j])))
    return out


def case_full(sa, rep, a):
    rep.say("## 10 MHz to %.2f GHz" % (sa.b_top / GHZ))
    rep.say()
    sig = [Signal(100.3 * MHZ, -35.0), Signal(915.2 * MHZ, -25.0),
           Signal(1575.4 * MHZ, -60.0), Signal(2.4401 * GHZ, -30.0),
           Signal(5.2003 * GHZ, -45.0)]
    t0 = time.time()
    f, p1, _ = sa.sweep(sig, 10 * MHZ, sa.b_top, image_reject=False)
    _, p, clip = sa.sweep(sig, 10 * MHZ, sa.b_top, image_reject=True)
    took = time.time() - t0
    rows = [("%.1f" % (s.f / MHZ), "%.1f" % s.dbm,
             "%.1f" % reading(f, p, s.f)) for s in sig]
    rep.table(["signal, MHz", "level, dBm", "reads, dBm"], rows)
    found = sorted(spurs(sa, sig, f, p1), key=lambda x: -x[1])
    left = spurs(sa, sig, f, p)
    rows = []
    for fs, ps in found[:16]:
        rows.append(("%.2f" % (fs / MHZ), "%.1f" % ps,
                     "%.1f" % reading(f, p, fs, 15e3),
                     why(sa, sig, fs, ps)))
    rep.say("Readings 15 dB or more over the local floor that are not the "
            "five signals: %d with one capture per step, %d with two."
            % (len(found), len(left)))
    rep.say()
    if rows:
        rep.table(["at, MHz", "one capture, dBm", "two captures, dBm",
                   "made by"], rows)
    rep.say("%d captures of %d samples, %.0f s of simulation."
            % (3 * len(sa.centres(10 * MHZ, sa.b_top)), sa.nfft, took))
    rep.say()
    fig = spectrum_figure([(f, p1, "one capture per step"),
                           (f, p, "two captures, the lower kept")],
                          "Five signals, 10 MHz to %.2f GHz"
                          % (sa.b_top / GHZ),
                          marks=[(s.f, reading(f, p, s.f),
                                  "%.0f dBm" % s.dbm) for s in sig])
    rep.figure(fig, "full.png", "Full sweep")


def case_strong(sa, rep, a):
    lv, att, fs = 0.0, 20.0, 915.2 * MHZ
    rep.say("## One strong signal: %g dBm at %.1f MHz, attenuator %g dB"
            % (lv, fs / MHZ, att))
    rep.say()
    s2 = Analyzer(lo1=sa.lo1, lo2=sa.lo2, att_db=att, seed=sa.seed,
                  if_att_db=sa.if_att_db, extra_lpf=sa.extra_lpf,
                  pad1_db=sa.pad1_db, pad2_db=sa.pad2_db)
    sig = [Signal(fs, lv)]
    f, p1, _ = s2.sweep(sig, 10 * MHZ, s2.b_top, image_reject=False)
    _, p, clip = s2.sweep(sig, 10 * MHZ, s2.b_top, image_reject=True)
    found = sorted(spurs(s2, sig, f, p1), key=lambda x: -x[1])
    left = sorted(spurs(s2, sig, f, p), key=lambda x: -x[1])
    rep.say("The mixer sees %.0f dBm. The signal reads %.1f dBm. Other "
            "readings 15 dB or more over the local floor: %d with one "
            "capture per step, %d with two." % (
                lv - att - 5, reading(f, p, fs), len(found), len(left)))
    rep.say()
    rows = []
    kept = {round(x[0] / 1e5) for x in left}
    for fx, px in found[:20]:
        both = reading(f, p, fx, 15e3)
        rows.append(("%.2f" % (fx / MHZ), "%.1f" % px, why(s2, sig, fx, px),
                     "%.1f" % both, why(s2, sig, fx, both, shifted=True)
                     if round(fx / 1e5) in kept else ""))
    if rows:
        rep.table(["at, MHz", "one capture, dBm", "made by",
                   "two captures, dBm", "and in the second capture by"],
                  rows)
    rep.say("`1LO2-1[1LO1-1[x]]` is the plain conversion of x: where it is "
            "not at x it is the second mixer's image. What survives two "
            "captures does so because the IF is a quarter of the sampling "
            "rate: the image in the first capture and the alias (the signal "
            "at an IF of 25 MHz, folded) in the second land on the same "
            "frequency, so the image rejection is no better than the "
            "anti-alias filter's stop band.")
    rep.say()
    fig = spectrum_figure([(f, p1, "one capture per step"),
                           (f, p, "two captures, the lower kept")],
                          "A %g dBm signal at %.1f MHz, attenuator %g dB"
                          % (lv, fs / MHZ, att))
    rep.figure(fig, "strong.png", "One strong signal")


CASES = dict(budget=case_budget, cw=case_cw, im3=case_im3,
             compression=case_compression, feedthrough=case_feedthrough,
             residuals=case_residuals, bandb=case_bandb, full=case_full,
             strong=case_strong)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cases", nargs="*", help="default: all of them")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--att", type=float, default=10.0)
    ap.add_argument("--if-att", type=float, default=6.0)
    ap.add_argument("--preamp", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--lo1", choices=("lmx2582", "lmx2572"),
                    default="lmx2582")
    ap.add_argument("--lo2", choices=("lmx2582", "lmx2572"),
                    default="lmx2582")
    ap.add_argument("--pads", type=float, nargs=2, default=(3.0, 3.0),
                    metavar="DB", help="the pads at the first mixer's IF "
                    "and RF ports")
    ap.add_argument("--extra-lpf", action="store_true",
                    help="a printed 4 GHz low-pass after the two LFCN-2250+")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out"))
    a = ap.parse_args(argv)
    if a.list:
        print("\n".join(CASES))
        return 0
    bad = [c for c in a.cases if c not in CASES]
    if bad:
        ap.error("no such case: %s (--list)" % ", ".join(bad))
    sa = Analyzer(lo1=getattr(lo, a.lo1)(), lo2=getattr(lo, a.lo2)(),
                  att_db=a.att, if_att_db=a.if_att, preamp=a.preamp,
                  seed=a.seed, extra_lpf=a.extra_lpf, pad1_db=a.pads[0],
                  pad2_db=a.pads[1])
    rep = Report(a.out)
    rep.say("# Spectrum analyzer simulation")
    rep.say()
    rep.say("LO1 %s, LO2 %s, two SIM-83+, 40 MSPS %s, %d-point captures."
            % (sa.lo1.name, sa.lo2.name, sa.adc.name, sa.nfft))
    rep.say()
    for name in (a.cases or list(CASES)):
        t0 = time.time()
        CASES[name](sa, rep, a)
        print("[%s: %.0f s]" % (name, time.time() - t0), file=sys.stderr)
    rep.say("## Where each part's numbers come from")
    rep.say()
    rep.table(["part", "source"], parts.provenance(sa.all_parts()))
    rep.save()
    print("wrote %s" % os.path.join(a.out, "report.md"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
