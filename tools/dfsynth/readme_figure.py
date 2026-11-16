#!/usr/bin/env python3
"""The README's figure: four filters as dfsynth lays them out, over their line model's response.

    python tools/readme_figure.py [out.png]          # default docs/filters.png
"""
import json, os, subprocess, sys, tempfile

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon

CASES = [('stepped low-pass, 2 GHz', '--type lowpass --topology stepped --f0 2G --order 7 --substrate RO4003C-0.508'),
         ('stub band-stop, 2.4 GHz', '--type bandstop --topology stub --f0 2.4G --bw 1G --substrate RO4003C-0.508'),
         ('hairpin band-pass, 2.4 GHz', '--type bandpass --topology hairpin --f0 2.4G --bw 240M --substrate RO4003C-0.813'),
         ('interdigital band-pass, 5 GHz', '--type bandpass --topology interdigital --f0 5G --bw 500M --substrate RO4003C-0.813')]


def main(out):
    fig, axs = plt.subplots(2, len(CASES), figsize=(16, 6.4), gridspec_kw=dict(height_ratios=(1.2, 1)))
    with tempfile.TemporaryDirectory() as tmp:
        for k, (title, args) in enumerate(CASES):
            base = os.path.join(tmp, f'f{k}')
            subprocess.run([sys.executable, '-m', 'dfsynth', *args.split(), '-o', base], check=True, capture_output=True)
            d = json.load(open(base + '_design.json', encoding='utf-8'))
            g, (W, H) = d['geometry'], d['board_mm']
            ax = axs[0, k]
            ax.add_patch(Polygon([(0, 0), (W, 0), (W, H), (0, H)], fc='#2e6b3f', ec='none'))
            for p in g['polys']:
                ax.add_patch(Polygon(p, fc='#d9a441', ec='#8a5d14', lw=0.4))
            for x, y in g['vias']:
                ax.add_patch(Circle((x, y), g['via_mm'][1] / 2, fc='#bbbbbb', ec='k', lw=0.3))
            for n, x, y, _ in g['ports']:
                ax.annotate(f'P{n}', (x, y), color='w', ha='left' if x < W / 2 else 'right', va='center', fontsize=8,
                            weight='bold')
            ax.set(xlim=(0, W), ylim=(H, 0), aspect='equal', title=f'{title}\n{W:.0f} x {H:.0f} mm')
            ax.axis('off')
            s = np.loadtxt(base + '_model.s2p', comments=('!', '#'))
            ax = axs[1, k]
            ax.plot(s[:, 0], 20 * np.log10(np.abs(s[:, 3] + 1j * s[:, 4])), label='S21')
            ax.plot(s[:, 0], 20 * np.log10(np.abs(s[:, 1] + 1j * s[:, 2])), label='S11', lw=0.9)
            for f in d['targets_GHz'].values():
                ax.axvline(f, color='k', ls=':', lw=0.8)
            ax.set(ylim=(-50, 2), xlabel='GHz', ylabel='dB' if k == 0 else None)
            ax.grid(alpha=0.3)
    axs[1, 0].legend(loc='lower left')
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    print('wrote', out)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'docs', 'filters.png'))
