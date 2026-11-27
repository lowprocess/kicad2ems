#!/usr/bin/env python3
"""The free-form section's figure: a free-form design's board beside the
conventional layout of the same spec (to one scale), over the pixel model's
response and a full-wave result's.

    python tools/dfsynth/freeform_figure.py BASE [out.png] [--against TOPOLOGY]
        # BASE: a `dfsynth --topology freeform -o BASE --validate mom` run
        # (BASE_design.json, BASE_model.s2p, BASE_em*/); default
        # docs/dfsynth/freeform.png; --against: the conventional topology
        # drawn beside it (default: hairpin, a low-pass: stepped)
"""
import glob, json, os, subprocess, sys, tempfile

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon


def board(ax, d, title):
    g, (W, H) = d['geometry'], d['board_mm']
    ax.add_patch(Polygon([(0, 0), (W, 0), (W, H), (0, H)], fc='#2e6b3f',
                         ec='none'))
    for p in g['polys']:
        ax.add_patch(Polygon(p, fc='#d9a441', ec='none'))
    for n, x, y, _ in g['ports']:
        ax.annotate(f'P{n}', (x, y), color='w', va='center', fontsize=8,
                    ha=('left' if x < W / 2 else 'right'), weight='bold')
    ax.set(xlim=(0, W), ylim=(H, 0), aspect='equal')
    ax.set_title(title, fontsize=10)
    ax.axis('off')
    return W, H


def copper_box(d):
    """(width, height) of the copper between the feeds, mm."""
    P = np.array([p for poly in d['geometry']['polys'] for p in poly])
    feed = max(5.0, 4 * d['substrate']['h'])
    return np.ptp(P[:, 0]) - 2 * feed, np.ptp(P[:, 1])


def s2p(path):
    s = np.loadtxt(path, comments=('!', '#'))
    unit = 1.0
    for line in open(path):
        if line.startswith('#'):
            unit = {'HZ': 1e-9, 'KHZ': 1e-6, 'MHZ': 1e-3,
                    'GHZ': 1.0}[line.split()[1].upper()]
            ri = 'RI' in line.upper()
            break
    c = lambda a, b: (a + 1j * b) if ri else a * np.exp(1j * np.radians(b))
    return s[:, 0] * unit, c(s[:, 1], s[:, 2]), c(s[:, 3], s[:, 4])


def main(base, out, against=None):
    d = json.load(open(base + '_design.json', encoding='utf-8'))
    sp = d['spec']
    against = against or ('stepped' if sp['kind'] == 'lowpass' else 'hairpin')
    with tempfile.TemporaryDirectory() as tmp:
        ref = os.path.join(tmp, 'ref')
        subprocess.run(
            [sys.executable, '-m', 'kicad2ems.dfsynth', '--type', sp['kind'],
             '--topology', against, '--f0', str(sp['f0']), '--order',
             str(sp['order']), '--substrate', d['substrate']['name'], '-o',
             ref, '--allow-fab-violations']
            + (['--bw', str(sp['bw'])] if sp['bw'] else []),
            check=True, capture_output=True)
        r = json.load(open(ref + '_design.json', encoding='utf-8'))
    fig = plt.figure(figsize=(12, 7.2))
    gs = fig.add_gridspec(2, 2, height_ratios=(1, 1.15),
                          width_ratios=(r['board_mm'][0], d['board_mm'][0]))
    bw, bh = copper_box(d)
    rw, rh = copper_box(r)
    a0, a1 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
    board(a0, r, f"dfsynth {against}: {rw:.1f} x {rh:.1f} mm between the "
          f"feeds ({rw * rh:.0f} mm²)")
    board(a1, d, f"free-form: {bw:.1f} x {bh:.1f} mm ({bw * bh:.0f} mm²)")
    H = max(r['board_mm'][1], d['board_mm'][1])
    for a, x in ((a0, r), (a1, d)):   # one scale: the same height of board
        c = x['board_mm'][1] / 2
        a.set_ylim(c + H / 2, c - H / 2)
    ax = fig.add_subplot(gs[1, :])
    f, s11, s21 = s2p(base + '_model.s2p')
    db = lambda x: 20 * np.log10(np.maximum(np.abs(x), 1e-9))
    ax.plot(f, db(s21), '--', color='#d62728', lw=1.4,
            label='pixel model S21')
    ax.plot(f, db(s11), '--', color='#d62728', lw=0.8, alpha=0.6,
            label='pixel model S11')
    ems = sorted(glob.glob(base + '_em*'), key=os.path.getmtime)
    for path in (glob.glob(os.path.join(ems[-1], '*_mom25d.s2p'))
                 if ems else []):
        f, s11, s21 = s2p(path)
        ax.plot(f, db(s21), color='#1f77b4', lw=1.8, label='mom25d S21')
        ax.plot(f, db(s11), color='#1f77b4', lw=0.9, alpha=0.7,
                label='mom25d S11')
    for x in d['targets_GHz'].values():
        ax.axvline(x, color='k', ls=':', lw=0.8)
    ax.set(ylim=(-60, 2), xlabel='GHz', ylabel='dB')
    ax.grid(alpha=0.3)
    ax.legend(loc='lower left', ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    print('wrote', out)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    against = None
    if '--against' in args:
        i = args.index('--against')
        against = args[i + 1]
        del args[i:i + 2]
    main(args[0], args[1] if len(args) > 1 else os.path.join(
        os.path.dirname(__file__), '..', '..', 'docs', 'dfsynth',
        'freeform.png'), against)
