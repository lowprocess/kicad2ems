#!/usr/bin/env python3
"""Pictures for the docs drawn from kicad2ems itself (the others: tools/xsection_figures.py, and kicad2ems-gui's
tools/readme_media.py for the window):

    python tools/doc_figures.py gallery     docs/img/examples_rf.png, examples_antennas.png: every example's board
    python tools/doc_figures.py solvers     docs/img/solvers.png: each solver's distance from openEMS and its time
"""
import os, shutil, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import matplotlib                                                       # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt                                         # noqa: E402
from matplotlib.patches import PathPatch                                # noqa: E402
from matplotlib.path import Path                                        # noqa: E402

IMG = os.path.join(REPO, 'docs', 'img')
# layer colours, drawn bottom first: the top copper strongest
LAYERS = {'B.Cu': '#c9c2b2', 'In2.Cu': '#9fb8d6', 'In1.Cu': '#7fa3cf', 'F.Cu': '#c8782a'}


def _patch(geo, colour):
    """A GeoJSON polygon or multipolygon (with holes) as one patch."""
    polys = geo['coordinates'] if geo['type'] == 'MultiPolygon' else [geo['coordinates']]
    verts, codes = [], []
    for poly in polys:
        for ring in poly:
            verts += list(ring)
            codes += [Path.MOVETO] + [Path.LINETO] * (len(ring) - 2) + [Path.CLOSEPOLY]
    return PathPatch(Path(verts, codes), fc=colour, ec='none')


def draw_board(ax, path, title):
    from kicad2ems.api.frontend import Board, KiCadBoard
    d = Board(KiCadBoard(path), log=lambda *a: None, need_ports=False).to_dict()
    (xs, ys), = [zip(*d['outline']['coordinates'][0])]
    ax.fill(xs, ys, color='#2e6b3f', zorder=0)                          # the substrate
    for layer, colour in LAYERS.items():
        for c in d['copper'].get(layer, []):
            ax.add_patch(_patch(c['polygon'], colour))
    for p in d['ports']:
        ax.plot(*p['c'], 'o', ms=3, color='#e0479e', zorder=5)
    ax.set(xlim=(min(xs), max(xs)), ylim=(min(ys), max(ys)), aspect='equal')
    ax.set_title(title, fontsize=8)
    ax.axis('off')


def gallery():
    from examples import examples, board_of, make_boards, BOARDS
    tmp = tempfile.mkdtemp(prefix='k2e-gallery-')
    try:
        make_boards(tmp, log=None)                                      # the generated boards, made afresh
        for stem in BOARDS:
            shutil.copy(os.path.join(REPO, 'examples', stem + '.kicad_pcb'), tmp)
        ex = examples()
        for name, keep in (('examples_rf.png', lambda n: not n.startswith('antenna_')),
                           ('examples_antennas.png', lambda n: n.startswith('antenna_'))):
            names = [n for n in ex if keep(n)]
            cols = 7
            rows = -(-len(names) // cols)
            fig, axs = plt.subplots(rows, cols, figsize=(2.0 * cols, 1.75 * rows))
            for ax in axs.flat:
                ax.axis('off')
            for ax, n in zip(axs.flat, names):
                draw_board(ax, os.path.join(tmp, board_of(ex[n])), n)
            fig.tight_layout()
            fig.savefig(os.path.join(IMG, name), dpi=80)
            plt.close(fig)
            print('wrote', os.path.join('docs', 'img', name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# docs/usage.md#which: the largest |dS| from openEMS over S11 and S21, and wall time (s), per example
OPENEMS = {'sma_launch': 21.5, 'hairpin': 45, 'rf_wilkinson': 33, 'rf_coupler': 26, 'filter': 118,
           'rf_branchline': 8.2, 'antenna_patch': 25, 'demo_bandpass': 84}
SOLVERS = {'gfdtd (GPU)': ('#1f77b4', {'sma_launch': (0.001, 2.8), 'hairpin': (0.002, 5.5), 'rf_wilkinson': (0.006, 4.2),
                                       'rf_coupler': (0.006, 2.9), 'filter': (0.012, 10.2), 'rf_branchline': (0.012, 0.7),
                                       'antenna_patch': (0.020, 2.6), 'demo_bandpass': (0.126, 2.4)}),
           'mom25d (GPU)': ('#d62728', {'sma_launch': (0.066, 108), 'hairpin': (0.106, 7.0), 'rf_wilkinson': (0.040, 4.5),
                                        'rf_coupler': (0.045, 10.1), 'filter': (0.267, 14.7), 'rf_branchline': (0.052, 23.0),
                                        'antenna_patch': (0.224, 9.3), 'demo_bandpass': (0.093, 36.3)}),
           'line model': ('#2ca02c', {'sma_launch': (0.47, 0.05), 'hairpin': (0.65, 0.3), 'rf_wilkinson': (0.09, 0.3),
                                      'rf_coupler': (0.09, 0.1), 'filter': (0.82, 0.4), 'rf_branchline': (0.06, 0.2),
                                      'demo_bandpass': (0.47, 0.2)})}


def solvers():
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for label, (colour, pts) in SOLVERS.items():
        ax.scatter([t for _, t in pts.values()], [d for d, _ in pts.values()], color=colour, label=label, zorder=3)
        for ex, (d, t) in pts.items():
            ax.annotate(ex, (t, d), xytext=(3, 2), textcoords='offset points', fontsize=6.5, color=colour)
    ax.scatter(list(OPENEMS.values()), [7e-4] * len(OPENEMS), marker='|', s=200, color='#555',
               label='openEMS (the reference): its time')
    ax.set(xscale='log', yscale='log', xlabel='wall time (s): 24 CPU threads, an RX 7800 XT for gfdtd and mom25d',
           ylabel='largest |ΔS| from openEMS (S11, S21)', ylim=(5e-4, 1.5))
    ax.grid(alpha=0.3, which='both')
    ax.legend(fontsize=8, loc='lower left')
    fig.tight_layout()
    fig.savefig(os.path.join(IMG, 'solvers.png'), dpi=100)
    plt.close(fig)
    print('wrote', os.path.join('docs', 'img', 'solvers.png'))


if __name__ == '__main__':
    what = sys.argv[1:] or ['gallery', 'solvers']
    for w in what:
        {'gallery': gallery, 'solvers': solvers}[w]()
