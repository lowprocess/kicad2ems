"""Surface-current maps of a finished segment run, 2-D, each copper layer, with
no sweep solved again:

  1. the run's network rebuilt from its folder (kicad2ems.core.segment.run on
     the same arguments: every piece solved before is read back, by its hash;
     its join is caught before it writes anything);
  2. every chain driven at once from its start (the port whose net reads as
     an input, else its first; 1 W incident each, in phase, every other port
     matched: one excitation), the waves at every cut of the joined network,
     at one frequency (--mode power: each chain alone, their maps' powers
     summed);
  3. each mom25d layer piece built again from its board file and its run's own
     settings and solved at that frequency once (every port's 1 V gap source
     at once; its S checked against the run's), its currents those solutions
     weighted by its ports' voltages, sqrt(z0) (a + b);
  4. |J| (A/m) on its triangles, drawn over the board in dB of its 99.5th
     percentile; one PNG a layer, and the triangles' values (npz).

The via models (the physics-based ones) have no mesh: their places are marked.
With --animate N (36 by default) also one cycle at the frequency, N frames: the
instantaneous current |Re(J e^(j phase))| on a linear scale, 1 the copper's
90th-percentile amplitude (MP4 and GIF).
usage:
  current_maps.py GHZ[,GHZ...] [--channels NET,...] [--range 50] [--name lime] \\
                  [--animate 36] [--mode together|power] [--starts P1,...] -- \\
                  SEGMENT ARGUMENTS (the run's own, its --out among them)
e.g.
  current_maps.py 2.4 -- boards/Lime_RFE.kicad_pcb --nets "$NETS" --pour keep \\
                  --thickness grow --fmin 0.1 --fmax 6 --points 201 --out lime
"""
import argparse
import json
import os
import re
import sys
import time

import numpy as np


class _Caught(Exception):
    pass


def network(seg_args, echo=print):
    """The run's network: (blocks [(S (nf, n, n), names)], pairs, parts, a),
    its pieces read back (solved again only where their hash changed:
    said)."""
    from kicad2ems.core import segment as seg
    got = {}
    join, solve_all = seg.join, seg._solve_all

    def caught_join(blocks, pairs):
        got['blocks'], got['pairs'] = blocks, pairs
        raise _Caught

    def kept_solve_all(parts, out, band, a, echo_):
        got['parts'], got['band'] = parts, band
        return solve_all(parts, out, band, a, echo_)
    seg.join, seg._solve_all = caught_join, kept_solve_all
    a = seg.parser().parse_args(seg_args)
    a.config = None                    # (segment.main's)
    if a.board.endswith('.toml'):
        a.config, a.board = a.board, None
    kept = set()
    try:
        seg.run(a, echo=lambda t: kept.add(t.split(':')[0])
                if 'solved before' in t else None)
    except _Caught:
        pass
    finally:
        seg.join, seg._solve_all = join, solve_all
    fresh = [p['name'] for p in got['parts']
             if p['kind'] == 'layer' and p['name'] not in kept]
    if fresh:
        echo(f"current maps: {len(fresh)} piece(s) were not in the run's "
             "folder as solved (now they are): " + ', '.join(fresh[:8]))
    return got['blocks'], got['pairs'], got['parts'], got['band'], a


def waves(blocks, pairs, fi, drives):
    """The incident waves a at every port of the blocks, at frequency index
    fi, for each drive (an external port's name: 1 there, 0 at the others):
    ({name: column}, A (n, len(drives)), B = S A)."""
    from scipy import sparse
    from scipy.sparse.linalg import splu
    names = [n for _, ns in blocks for n in ns]
    at = {n: i for i, n in enumerate(names)}
    big = sparse.block_diag([sparse.csr_matrix(S[fi]) for S, _ in blocks],
                            format='csr')
    n = len(names)
    inner = [at[p] for pr in pairs for p in pr]
    isin = np.zeros(n, bool)
    isin[inner] = True
    outer = np.flatnonzero(~isin)
    # the waves into a linked port are the waves out of its partner: a_i = G
    # b_i, b = S a, so (I - G S_ii) a_i = G S_ie a_e
    pos = {p: j for j, p in enumerate(inner)}
    r = [pos[at[x]] for x, y in pairs] + [pos[at[y]] for x, y in pairs]
    c = [pos[at[y]] for x, y in pairs] + [pos[at[x]] for x, y in pairs]
    G = sparse.csr_matrix((np.ones(len(r)), (r, c)),
                          shape=(len(inner), len(inner)))
    S_ii = big[inner][:, inner]
    S_ie = big[inner][:, outer].toarray()
    ext = {names[i].split(':', 1)[1]: k for k, i in enumerate(outer)}
    Ae = np.zeros((len(outer), len(drives)), complex)
    for j, d in enumerate(drives):
        Ae[ext[d], j] = 1.0
    lhs = (sparse.identity(len(inner), format='csc') - G @ S_ii).tocsc()
    Ai = splu(lhs).solve(G @ (S_ie @ Ae)) if len(inner) else np.zeros(
        (0, len(drives)))
    A = np.zeros((n, len(drives)), complex)
    A[inner] = Ai
    A[outer] = Ae
    return at, A, big @ A


def piece_sim(folder, name, f):
    """A mom25d layer piece built again from its board file and its run's
    settings (run.json): (model, sim, the run's port names in its S's order)."""
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.pipeline import board_from_settings
    from kicad2ems.mom25d.api import build_board
    from kicad2ems.mom25d.solvers.mom import _board_kw, _mesh_kw
    rec = json.load(open(os.path.join(folder, f"{name}_run.json")))
    st = dict(rec['settings'])
    st['board'] = os.path.join(folder, f"{name}_board.json")
    s = build_settings(st)
    m = board_from_settings(s, log=lambda *x: None)
    pairs, kw = _board_kw(m, s.mom_dmax, s.mom_port, **_mesh_kw(s, m))
    if pairs:
        raise ValueError(f"{name}: differential ports (pairs): not drawn")
    freq = np.linspace(s.fmin, s.fmax, int(s.points)) * 1e9
    model, sim = build_board(m.to_dict(), float(freq[0]), float(freq[-1]),
                             log=lambda *x: None, **kw)
    return model, sim, [p.name for p in m.ports]


# a port whose net reads as a chain's start (its input): IN as a word of
# the net's last name (RF_IN, /IN, TXIN, LNA_IN) and no OUT
_IN = re.compile(r'(^|[_/\-.])(RF_?)?(TX|RX)?_?IN([_\d]|$)|IN$', re.I)


def starts(ps):
    """The port a chain is driven from (its port_nets entries ps): the one
    whose net reads as an input, else the lowest-numbered."""
    num = lambda p: int(''.join(c for c in p['port'] if c.isdigit()) or 0)
    ins = [p for p in ps if _IN.search(p['net'].rsplit('/', 1)[-1])
           and 'OUT' not in p['net'].upper()]
    return min(ins or ps, key=num)['port']


def triangle_current(sim, I):
    """J (A/m) at each triangle's centroid, x and y (Nt x k complex each),
    from the RWG coefficients I (N x k; the via chains' rows left out)."""
    r = sim.rwg
    Ne = sim.Ne
    I = np.asarray(I).reshape(I.shape[0], -1)[:Ne]
    Nt = len(r.tris)
    Jx = np.zeros((Nt, I.shape[1]), complex)
    Jy = np.zeros_like(Jx)
    for tri, sign, v in ((r.tp, 1.0, r.vp), (r.tm, -1.0, r.vm)):
        a = sign * r.l / (2 * r.area[tri])
        dx = r.cent[tri, 0] - v[:, 0]
        dy = r.cent[tri, 1] - v[:, 1]
        np.add.at(Jx, tri, (a * dx)[:, None] * I)
        np.add.at(Jy, tri, (a * dy)[:, None] * I)
    return Jx, Jy


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--' not in argv:
        raise SystemExit(__doc__)
    i = argv.index('--')
    ap = argparse.ArgumentParser()
    ap.add_argument('ghz', help="GHz, comma-separated (each piece built once, solved at each)")
    ap.add_argument('--channels', default='',
                    help="channels (any of their nets), comma-separated: "
                    "every channel by default")
    ap.add_argument('--range', type=float, default=50.0, help="dB shown")
    ap.add_argument('--starts', default='',
                    help="the chains' start ports by hand (P5,P9,...: a "
                    "chain with one of these is driven from it)")
    ap.add_argument('--animate', type=int, default=36,
                    help="frames of one cycle at the frequency (the "
                    "instantaneous current, |Re(J e^(j phase))|; --mode "
                    "together alone; 0: none): an MP4 and a GIF a layer")
    ap.add_argument('--mode', choices=('together', 'power'),
                    default='together',
                    help="together: every chain driven at once from its start "
                    "(in phase, its other ends matched: one excitation); "
                    "power: each chain on its own, their powers summed")
    ap.add_argument('--name', default='current')
    o = ap.parse_args(argv[:i])
    t0 = time.time()
    blocks, pairs, parts, band, a = network(argv[i + 1:])
    out = os.path.abspath(a.out)
    res = json.load(open(os.path.join(out, 'segment.json')))
    f = np.linspace(band['fmin'], band['fmax'], int(band['points']))
    fis = sorted({int(np.argmin(np.abs(f - float(g))))
                  for g in o.ghz.split(',') if g.strip()})
    # the chains: their ports by chain (port_nets' nets), each driven from
    # its start (starts())
    want = [c.strip() for c in o.channels.split(',') if c.strip()]
    chans = {}
    for p in res['port_nets']:
        key = tuple(p.get('nets') or [p['net']])
        if want and not any(w in key for w in want):
            continue
        chans.setdefault(key, []).append(p)
    hand = {x.strip() for x in o.starts.split(',') if x.strip()}
    drives = [next((p['port'] for p in ps if p['port'] in hand), None)
              or starts(ps) for ps in chans.values()]
    with open(os.path.join(out, f"{o.name}_starts.txt"), 'w') as fh:
        for ps, d in zip(chans.values(), drives):
            p = next(q for q in ps if q['port'] == d)
            fh.write(f"{d}\t{p['net']}\t{p.get('ref', '')}.{p.get('pad', '')}"
                     f"\t-> " + ', '.join(f"{q['port']} {q.get('ref', '')}"
                                           for q in ps if q['port'] != d)
                     + '\n')
    print(f"current maps: {len(drives)} chain(s) at "
          f"{', '.join(f'{f[i]:.4g}' for i in fis)} GHz, "
          f"{'all driven at once' if o.mode == 'together' else 'each alone'}"
          f", {len(blocks)} blocks, {len(pairs)} links", flush=True)
    z0 = 50.0
    V = {}                              # frequency index -> each port's voltage
    for fi in fis:
        at, A, B = waves(blocks, pairs, fi, drives)
        if o.mode == 'together':       # one excitation: every start at once
            A, B = A.sum(1, keepdims=True), B.sum(1, keepdims=True)
        V[fi] = np.sqrt(z0) * (A + B)
    layers = {fi: {} for fi in fis}     # fi -> layer -> [(xy tri, |J|^2, Jx, Jy)]
    marks = []
    for p in parts:
        name = p['name']
        if p['kind'] != 'layer':
            for v in p.get('vias', []):
                marks.append((v['x'], v['y']))
            continue
        folder = os.path.join(out, name)
        if not os.path.isfile(os.path.join(folder, f"{name}_run.json")):
            print(f"  {name}: no mom25d run here (not drawn)")
            continue
        # its block (its S's port order) in the network
        blk = next(((S, ns) for S, ns in blocks
                    if ns and ns[0].startswith(name + ':')), None)
        if blk is None:
            print(f"  {name}: not a block of its own (a window?): not drawn")
            continue
        Sb, ns = blk
        cols = [at[n] for n in ns]
        if not max(np.abs(V[fi][cols]).max() for fi in fis) > 1e-9:
            continue                     # (no drive reaches it)
        tp = time.time()
        model, sim, _ = piece_sim(folder, name, f[fis[0]] * 1e9)
        sim.reuse = 'off'
        sp = [q.name for q in sim.ports]
        order = [sp.index(n.split(':', 1)[1]) for n in ns]
        nodes = sim.rwg.nodes[:, :2] * 1e3
        lay = {k: l for l, k in model.iface.items()}
        dS = 0.0
        for fi in fis:
            fhz = f[fi] * 1e9
            Ic = sim.currents(fhz, sim.excitation())  # every port's 1 V one
            # (its S at this frequency again, against the one the join read)
            Sg = sim.sparams(fhz)[np.ix_(order, order)]
            dS = max(dS, np.abs(Sg - Sb[fi]).max())
            Ip = Ic[:, order] @ V[fi][cols]           # (unknowns, drives)
            Jx, Jy = triangle_current(sim, Ip)
            J2 = (np.abs(Jx) ** 2 + np.abs(Jy) ** 2).sum(1)
            # (the one excitation's complex J, for the phase animation)
            cx, cy = ((Jx[:, 0], Jy[:, 0]) if o.mode == 'together'
                      else (None, None))
            for k, idx in sim.tri_idx.items():
                if not len(idx) or k not in lay:
                    continue
                layers[fi].setdefault(lay[k], []).append(
                    (nodes[sim.rwg.tris[idx]], J2[idx],
                     None if cx is None else cx[idx],
                     None if cy is None else cy[idx]))
        del sim, model
        print(f"  {name}: |dS| against the run {dS:.1e}, "
              f"{time.time() - tp:.1f} s", flush=True)
    for fi in fis:
        if layers[fi]:
            _draw(layers[fi], marks, res, out, o, f[fi])
            if o.animate and o.mode == 'together':
                _animate(layers[fi], marks, res, out, o, f[fi])
    print(f"current maps: {time.time() - t0:.0f} s")


def _area(t):
    return 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                        - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))


def _draw(layers, marks, res, out, o, ghz):
    """One PNG a layer: |J| in dB of the 99.5th percentile (by area: a port's
    gap and the copper's corners peak far above the lines' own), over the
    selected nets' tracks, zoomed to the copper drawn."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection, LineCollection
    from kicad2ems.core.importers.kicad import KiCadBoard
    kb = KiCadBoard(res['board'])
    nets = {n for p in res['port_nets'] for n in (p.get('nets') or [p['net']])}
    alltri = np.concatenate([it[0] for v in layers.values() for it in v])
    allj = np.concatenate([it[1] for v in layers.values() for it in v])
    w = _area(alltri)
    order = np.argsort(allj)
    cum = np.cumsum(w[order]) / w.sum()
    ref = allj[order][min(len(order) - 1, np.searchsorted(cum, 0.995))]
    o.ref = ref                         # (the animation's scale too)
    for lay, items in sorted(layers.items()):
        tris = np.concatenate([it[0] for it in items])
        j2 = np.concatenate([it[1] for it in items])
        db = 10 * np.log10(np.maximum(j2, 1e-300) / ref)
        keep = db > -o.range - 10
        lo = tris[keep].reshape(-1, 2).min(0) - 3
        hi = tris[keep].reshape(-1, 2).max(0) + 3
        span = hi - lo
        fig, ax = plt.subplots(figsize=(16, 16 * span[1] / span[0] + 1.5)
                               if span[0] >= span[1] else
                               (16 * span[0] / span[1] + 3, 16), dpi=160)
        ax.set_facecolor('black')
        segs = [np.c_[np.asarray(t['pts'])[:, 0], -np.asarray(t['pts'])[:, 1]]
                for t in kb.tracks if t['layer'] == lay and t['net'] in nets]
        if segs:
            ax.add_collection(LineCollection(segs, colors='0.35', lw=0.4,
                                             zorder=1))
        for e in kb.edges:
            xy = np.asarray(getattr(e, 'coords', e))
            if xy.ndim == 2 and len(xy) > 1:
                ax.plot(xy[:, 0], -xy[:, 1], color='0.5', lw=0.6, zorder=1)
        pc = PolyCollection(tris[keep], array=np.minimum(db[keep], 0),
                            cmap='inferno', edgecolors='none',
                            clim=(-o.range, 0), zorder=2)
        ax.add_collection(pc)
        if marks:
            mx = np.array(marks)
            ax.plot(mx[:, 0], mx[:, 1], 'o', ms=2, mfc='none', mec='tab:cyan',
                    mew=0.5, zorder=3, label='via models (no mesh)')
            ax.legend(loc='lower right', fontsize=8)
        ax.set_xlim(lo[0], hi[0])
        ax.set_ylim(lo[1], hi[1])
        ax.set_aspect('equal')
        fig.colorbar(pc, ax=ax, shrink=0.6, label="|J|, dB of the 99.5th "
                     "percentile " + (
                         "(every chain driven at once from its start, 1 W "
                         "each, in phase; its other ends matched)"
                         if o.mode == 'together' else
                         "(each chain driven alone from its start, 1 W, the "
                         "others matched; powers summed)"))
        ax.set_title(f"{os.path.basename(res['board'])}: {lay}, {ghz:.4g} GHz "
                     "- surface current")
        ax.set_xlabel('x (mm)')
        ax.set_ylabel('y (mm, KiCad y up)')
        png = os.path.join(out, f"{o.name}_{lay.replace('.', '_')}_"
                           f"{ghz:.3g}GHz.png")
        fig.savefig(png, bbox_inches='tight')
        plt.close(fig)
        np.savez_compressed(png[:-4] + '.npz', tris=tris, J2=j2, ref=ref)
        print(f"current maps: {lay} -> {png}")


def _animate(layers, marks, res, out, o, ghz):
    """One cycle at the frequency, o.animate frames a layer: the instantaneous
    surface current |Re(J e^(j phase))| (A/m, the vector's length) on a linear
    scale, 1 the copper's 90th-percentile amplitude, as an MP4 (ffmpeg, where
    there is one) and a GIF."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import animation
    from matplotlib.collections import PolyCollection, LineCollection
    from kicad2ems.core.importers.kicad import KiCadBoard
    kb = KiCadBoard(res['board'])
    nets = {n for p in res['port_nets'] for n in (p.get('nets') or [p['net']])}
    for lay, items in sorted(layers.items()):
        if any(it[2] is None for it in items):
            continue
        tris = np.concatenate([it[0] for it in items])
        jx = np.concatenate([it[2] for it in items])
        jy = np.concatenate([it[3] for it in items])
        j2 = np.abs(jx) ** 2 + np.abs(jy) ** 2
        keep = 10 * np.log10(np.maximum(j2, 1e-300) / o.ref) > -o.range
        tris, jx, jy = tris[keep], jx[keep], jy[keep]
        # 1: the 90th percentile (by area) of the copper drawn's amplitude (a
        # linear scale: the pads' and corners' peaks saturate)
        a = _area(tris)
        pk = np.sqrt(np.abs(jx) ** 2 + np.abs(jy) ** 2)
        srt = np.argsort(pk)
        cum = np.cumsum(a[srt]) / a.sum()
        amp = pk[srt][min(len(srt) - 1, np.searchsorted(cum, 0.9))]
        lo = tris.reshape(-1, 2).min(0) - 3
        hi = tris.reshape(-1, 2).max(0) + 3
        span = hi - lo
        w = 12.0
        fig, ax = plt.subplots(figsize=(w, w * span[1] / span[0] * 0.85 + 1.0)
                               if span[0] >= span[1] else
                               (w * span[0] / span[1] + 2.5, w), dpi=110,
                               layout='constrained')
        ax.set_facecolor('black')
        segs = [np.c_[np.asarray(t['pts'])[:, 0], -np.asarray(t['pts'])[:, 1]]
                for t in kb.tracks if t['layer'] == lay and t['net'] in nets]
        if segs:
            ax.add_collection(LineCollection(segs, colors='0.3', lw=0.4,
                                             zorder=1))
        pc = PolyCollection(tris, array=np.zeros(len(tris)), cmap='inferno',
                            edgecolors='none', clim=(0, 1), zorder=2)
        ax.add_collection(pc)
        if marks:
            mx = np.array(marks)
            ax.plot(mx[:, 0], mx[:, 1], 'o', ms=2, mfc='none', mec='tab:cyan',
                    mew=0.5, zorder=3)
        ax.set_xlim(lo[0], hi[0])
        ax.set_ylim(lo[1], hi[1])
        ax.set_aspect('equal')
        fig.colorbar(pc, ax=ax, shrink=0.8, label="|Re(J e^(j phase))|, of "
                     "the copper's 90th-percentile amplitude (every chain "
                     "driven at once from its start, 1 W each)")
        title = ax.set_title('')
        phases = np.linspace(0, 2 * np.pi, o.animate, endpoint=False)

        def frame(i):
            e = np.exp(1j * phases[i])
            v = np.hypot((jx * e).real, (jy * e).real) / amp
            pc.set_array(np.minimum(v, 1.0))
            title.set_text(f"{os.path.basename(res['board'])}: {lay}, "
                           f"{ghz:.4g} GHz, phase {np.degrees(phases[i]):3.0f}"
                           " deg - instantaneous surface current")
            return pc, title
        ani = animation.FuncAnimation(fig, frame, frames=len(phases),
                                      blit=False)
        base = os.path.join(out, f"{o.name}_{lay.replace('.', '_')}_"
                            f"{ghz:.3g}GHz_cycle")
        if animation.writers.is_available('ffmpeg'):
            ani.save(base + '.mp4', writer=animation.FFMpegWriter(
                fps=12, bitrate=4000))
            print(f"current maps: {lay} cycle -> {base}.mp4")
        ani.save(base + '.gif', writer=animation.PillowWriter(fps=12),
                 dpi=80)
        plt.close(fig)
        print(f"current maps: {lay} cycle -> {base}.gif")


if __name__ == '__main__':
    main()
