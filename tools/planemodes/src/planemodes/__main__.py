"""Command line.

    planemodes [board]                 open the viewer (optionally with a board)
    planemodes run board --fmin 1 --fmax 10 [--pairs F.Cu/In1.Cu,...] --out results/
                                       solve without the GUI, write modes.csv,
                                       summary.json, fields.npz (and --images)
"""
import argparse
import os
import sys


def _run(a):
    from . import MeshConfig, SolverConfig, StudyConfig, load_board, run_study
    from .export import save_results
    board = load_board(a.board)
    for n in board.notes:
        print('note:', n)
    pairs = [tuple(p.split('/')) for p in a.pairs.split(',')] if a.pairs else None
    cfg = StudyConfig(
        fmin=a.fmin * 1e9, fmax=a.fmax * 1e9, pairs=pairs,
        ref_nets=[n.strip() for n in a.ref_nets.split(',')] if a.ref_nets else None,
        mesh=MeshConfig(max_cell=a.max_cell, cells_per_wavelength=a.cells, min_angle=a.min_angle,
                        refine_narrow=a.refine_narrow, narrow_cell=a.narrow_cell),
        solver=SolverConfig(backend=a.backend, max_modes=a.max_modes,
                            fringe=not a.no_fringe, radiation=not a.no_radiation))
    res = run_study(board, cfg, progress=lambda f, t: print(f'[{100 * f:3.0f} %] {t}', flush=True))
    files = save_results(res, a.out, board, cfg)
    if a.images:
        files += _images(res, a.out, contour_per=a.contours)
    print('\n'.join('wrote ' + f for f in files))


def _images(results, folder, width=1600, contour_per=0):
    """One PNG per mode (signed V, each mode's own scale); contour_per > 0
    adds contour lines every 1/contour_per of the scale (V < 0 dashed, the
    nodal line bold)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .raster import FieldSampler, colorize, contour_levels, contours
    out = []
    for r in results:
        s = FieldSampler(r.mesh.nodes, r.mesh.tris)
        (x0, y0), (x1, y1) = r.mesh.nodes.min(0), r.mesh.nodes.max(0)
        h = max(2, int(width * (y1 - y0) / (x1 - x0)))
        s.sample(x0, y0, x1, y1, width, h)
        for k in range(len(r.modes.f)):
            vals = s.values(r.modes.V[:, k])
            img = colorize(vals, r.modes.peak[k], 'signed', alpha_outside=0)
            p = os.path.join(folder, f"{r.cavity.name.replace('/', '_')}_mode{k + 1:03d}_"
                                     f"{r.modes.f[k] / 1e9:.3f}GHz.png")
            if contour_per > 0:
                fig = plt.figure(figsize=(width / 100, h / 100), dpi=100)
                ax = fig.add_axes((0, 0, 1, 1)); ax.set_axis_off()
                ax.imshow(img, extent=(x0, x1, y1, y0), interpolation='nearest')
                levels = list(contour_levels(r.modes.peak[k], contour_per)) + [0.0]
                for lev, xy in contours(vals, (x0, y0, x1, y1), levels, max_px=width):
                    ax.plot(xy[:, 0], xy[:, 1], color='#14181b', lw=2.0 if lev == 0 else 0.8,
                            ls='--' if lev < 0 else '-')
                ax.set_xlim(x0, x1); ax.set_ylim(y1, y0)
                fig.savefig(p, transparent=True)
                plt.close(fig)
            else:
                plt.imsave(p, img)
            out.append(p)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'run':
        p = argparse.ArgumentParser(prog='planemodes run')
        p.add_argument('board')
        p.add_argument('--fmin', type=float, default=1.0, help='GHz')
        p.add_argument('--fmax', type=float, default=10.0, help='GHz')
        p.add_argument('--pairs', help='e.g. F.Cu/In1.Cu,In1.Cu/In2.Cu (default: every plane pair)')
        p.add_argument('--ref-nets', help='ground nets, comma-separated (default: guessed)')
        p.add_argument('--max-cell', type=float, default=0.0, help='mm (0: from the wavelength)')
        p.add_argument('--cells', type=float, default=12.0, help='cells per wavelength')
        p.add_argument('--min-angle', type=float, default=25.0)
        p.add_argument('--refine-narrow', type=float, default=0.0, help='mm (0: off)')
        p.add_argument('--narrow-cell', type=float, default=0.3, help='mm')
        p.add_argument('--backend', default='auto', choices=['auto', 'superlu', 'pardiso'])
        p.add_argument('--max-modes', type=int, default=600)
        p.add_argument('--no-fringe', action='store_true', help='leave out the edge fringing')
        p.add_argument('--no-radiation', action='store_true', help='leave radiation out of Q')
        p.add_argument('--out', default='planemodes_out')
        p.add_argument('--images', action='store_true', help='also a PNG per mode')
        p.add_argument('--contours', type=int, default=0,
                       help='with --images: contour lines every 1/N of the scale (0: none)')
        return _run(p.parse_args(argv[1:]))
    from .gui import main as gui
    return gui(argv[0] if argv else None)


if __name__ == '__main__':
    sys.exit(main())
