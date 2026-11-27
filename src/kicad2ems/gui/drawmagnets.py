"""
The Draw page's ferrites and magnets, without Qt: a magnet drawn in 3D as two
halves (its poles, N red and S blue), the board core.magnets works their bias
out on, the side panel's readout of it and the plan's map of it.

    v, f = magnet_mesh(g, z0, z1, zm)    # two halves meeting at zm (no faces
                                         # between them)
    c = pole_colours(v, f, zm, top, bottom)  # (m, 4) RGBA, a face's
    top, bottom = poles(br)              # the N / S colours on top / below
    b, at = bias_board(layout, stack, shapes)   # board.solids: the drawn
                                         # solids the bias depends on; at:
                                         # shape index -> board.solids index
    summary = core.magnets.summary(b)
    text = readout(summary, shapes, i, at)   # what shape i's panel says
    x0, y0, x1, y1, h, inside = bias_map(b, shapes, i, at)
                                         # H0 (Oe) on a grid over its
                                         # ferrite (or every ferrite)

A magnet's remanence `br` is signed (core.shapes): + magnetised up (+z), its N
pole on top; - down. Its top half is drawn in its top pole's colour. The bias
is core.magnets' (along z, the ferrite saturated where the field allows):
the readout gives the internal field's range, the share of the ferrite that
is saturated, and the two frequencies of the gyrotropy, f0 = gamma H0 and
fm = gamma 4 pi Ms.
"""
import numpy as np

__all__ = ['FERRITE_ER', 'N_RGB', 'S_RGB', 'magnet_mesh', 'pole_colours',
           'bias_map',
           'poles', 'bearing', 'bias_board', 'solid_index', 'readout']

# a new ferrite's permittivity (a dielectric's default, 4.3, is a laminate's:
# a microwave ferrite's is 13-16)
FERRITE_ER = 15.0
# the poles' colours (RGB 0-255): north red, south blue (a compass's)
N_RGB, S_RGB = (215, 45, 45), (45, 90, 215)


def magnet_mesh(g, z0, z1, zm):
    """Polygon g extruded from z0 to z1 in two halves meeting at zm (vertices,
    faces wound outward): the faces between them left out, so each face is
    wholly above zm or wholly below it. zm not inside (z0, z1): one prism."""
    from kicad2ems.core.view3d import prism_fast
    z0, z1 = sorted((float(z0), float(z1)))
    if not z0 < zm < z1:
        return prism_fast(g, z0, z1)
    (vl, fl), (vh, fh) = prism_fast(g, z0, zm), prism_fast(g, zm, z1)
    v = np.concatenate([vl, vh])
    f = np.concatenate([fl, fh + len(vl)])
    keep = ~(np.abs(v[f][:, :, 2] - zm) < 1e-12).all(1)
    return v, f[keep]


def pole_colours(v, f, zm, top, bottom):
    """(m, 4) float32 RGBA of each face: `top` above zm, `bottom` below."""
    up = np.asarray(v, float)[np.asarray(f)][:, :, 2].mean(1) > zm
    return np.where(up[:, None], np.asarray(top, np.float32),
                    np.asarray(bottom, np.float32)).astype(np.float32)


def poles(br):
    """(top, bottom): the RGB of the pole on top and below of a magnet of
    remanence br (+: N on top)."""
    return (N_RGB, S_RGB) if br >= 0 else (S_RGB, N_RGB)


class _Board:
    """What core.solids and core.magnets read of a board: its outline (None:
    no board), dielectric layers and solids (core.shapes.apply's)."""

    def __init__(self, outline, stack):
        self.outline = outline
        self.layers = [dict(l) for l in stack.get('layers', [])]
        self.cu_z = dict(stack.get('cu_z', {}))
        self.copper, self.copper_nets, self.sel = {}, {}, None
        self.solids = []


def bearing(shapes, outline=None):
    """The indices of the drawn solids the bias depends on: every ferrite and
    magnet, and a solid drawn after a ferrite whose box meets the ferrite's
    (a later solid takes a ferrite's place where they overlap). A via fence
    beside a circulator is left out (core.solids would resolve it all: 300
    vias took the readout from 20 ms to 50). outline(s): a shape's outline
    (the page's kept ones; default Shape.outline)."""
    out = outline or (lambda s: s.outline())
    sol = [(i, s, out(s)) for i, s in enumerate(shapes) if s.solid]
    sol = [(i, s, g.bounds, sorted((s.z0, s.z1))) for i, s, g in sol
           if g is not None]
    fer = [(i, b, z) for i, s, b, z in sol if s.material == 'ferrite']
    meets = lambda b, z, c, w: (b[0] < c[2] and c[0] < b[2] and b[1] < c[3]
                                and c[1] < b[3] and z[0] < w[1]
                                and w[0] < z[1])
    return [i for i, s, b, z in sol
            if s.material in ('ferrite', 'magnet')
            or any(i > k and meets(b, z, c, w) for k, c, w in fer)]


def bias_board(layout, stack, shapes, outline=None):
    """(board, at): a board for core.magnets, the board's outline and stack
    (layout: the Draw page's board dict, or None) and the drawn solids the
    bias depends on (bearing's; a hidden one too: a run applies it) as
    core.shapes.apply makes them; at: {shape index: its board.solids
    index}."""
    from shapely.geometry import shape as geo
    from kicad2ems.core import shapes as sh
    o = (layout or {}).get('outline')
    b = _Board(geo(o) if o else None, stack)
    keep = bearing(shapes, outline)
    sh.apply(b, sh.Drawing([shapes[i] for i in keep]))
    return b, {i: k for k, i in enumerate(keep)}


def solid_index(shapes, outline=None):
    """{shape index: its board.solids index} of all the drawn solids
    (apply's order: the solids with an outline). outline(s): a shape's
    outline (default Shape.outline)."""
    out, k = {}, 0
    for i, s in enumerate(shapes):
        if s.solid and (outline(s) if outline else s.outline()) is not None:
            out[i] = k
            k += 1
    return out


def bias_map(board, shapes, i, at, n=32):
    """The internal field H0 over the ferrite of shape i (a magnet: over
    every ferrite), along its magnetisation, in Oe, on an n x n grid of cell
    centres at each ferrite piece's mid-height (core.magnets.bias; board, at:
    bias_board's): (x0, y0, x1, y1, h, inside): h (n rows along y, n columns
    along x) NaN outside the ferrite or where it is not saturated, inside
    the cells in the ferrite; None for no ferrite left (32 x 32 over a 3 mm
    puck under a 5 mm magnet: about 40 ms)."""
    import shapely
    from kicad2ems.core import magnets, solids
    s = shapes[i]
    if s.material == 'ferrite':
        want = {at.get(i)}
    else:
        want = {at[j] for j, t in enumerate(shapes)
                if t.solid and t.material == 'ferrite' and j in at}
    ps = solids.pieces(board)
    fer = [p for p in ps if p.ferrite is not None and p.index in want
           and p.z1 > p.z0]
    if not fer:
        return None
    b = np.array([p.polygon.bounds for p in fer])
    x0, y0, x1, y1 = (*b[:, :2].min(0), *b[:, 2:].max(0))
    xs = x0 + (np.arange(n) + 0.5) * (x1 - x0) / n
    ys = y0 + (np.arange(n) + 0.5) * (y1 - y0) / n
    X, Y = np.meshgrid(xs, ys)  # (rows: y, columns: x)
    owner = np.full(X.shape, -1)
    z = np.zeros(X.shape)
    for p in fer:
        inside = shapely.contains_xy(p.polygon, X, Y) & (owner < 0)
        owner[inside] = p.index
        z[inside] = (p.z0 + p.z1) / 2
    m = owner >= 0
    h = np.full(X.shape, np.nan)
    if m.any():
        r = magnets.bias(board, ps, np.c_[X[m], Y[m], z[m]], owner[m])
        sign = np.array([r['sign'].get(int(o), 1.0) for o in owner[m]])
        h[m] = r['hz'] * sign / magnets.OE
    return float(x0), float(y0), float(x1), float(y1), h, m


def _range(lo, mean, hi, sign=1.0):
    """'lo to hi Oe, mean m' of a field along the magnetisation (sign)."""
    a, b = sorted((lo * sign, hi * sign))
    return f"{a:.0f} to {b:.0f} Oe, mean {mean * sign:.0f}"


def _ferrite_line(r, magnets):
    """The readout of one ferrite's summary row r (core.magnets.summary's);
    magnets: 'none' (none drawn), 'zero' (none magnetised) or 'yes'."""
    fm = f"fm = γ4πMs {r['fm'] / 1e9:.2f} GHz"
    if magnets != 'yes':
        return ('No magnet drawn' if magnets == 'none' else
                'The magnets have no remanence (Br 0)') + (
                    f": the ferrite is unbiased ({fm}).")
    sign = r['sign'] or 1.0
    way = 'up (+z)' if sign > 0 else 'down (−z)'
    if r['internal_oe'] is None:
        return (f"Not saturated anywhere: the magnets' Hz over it "
                f"({_range(*r['applied_oe'])}) is less than its own "
                f"demagnetising field; the solver leaves it unbiased ({fm}).")
    sat = r['saturated']
    share = ('saturated throughout' if sat >= 0.999 else
             f"saturated over {100 * sat:.0f} % of it (the rest left "
             "unbiased: the field there is below its demagnetising field)")
    return (f"Internal field {_range(*r['internal_oe'], sign)}, magnetised "
            f"{way}, {share}; f0 = γH0 {r['f0'] / 1e9:.2f} GHz, {fm}; "
            f"applied {_range(*r['applied_oe'], sign)}.")


def readout(summary, shapes, i, at=None):
    """What the side panel says of shape i (a ferrite or a magnet) from
    core.magnets.summary's rows: a ferrite's internal field, saturation, f0
    and fm (unbiased with no magnet drawn); a magnet's direction and each
    ferrite's line. '' for anything else."""
    s = shapes[i]
    if s.material not in ('ferrite', 'magnet') or not s.solid:
        return ''
    at = solid_index(shapes) if at is None else at
    mags = [t for j, t in enumerate(shapes)
            if t.material == 'magnet' and j in at]
    magnets = ('none' if not mags else 'yes' if any(t.br for t in mags)
               else 'zero')
    rows = {r['index']: r for r in summary}
    if s.material == 'ferrite':
        r = rows.get(at.get(i))
        if r is None:
            return ('Nothing of it is left: later solids cover it (a later '
                    'solid wins where they overlap).')
        return _ferrite_line(r, magnets)
    way = ('Magnetised up (+z): N on top (red), S below (blue)' if s.br > 0
           else 'Magnetised down (−z): S on top (blue), N below (red)'
           if s.br < 0 else 'Not magnetised (Br 0)')
    name = {k: shapes[j].name or f"{shapes[j].kind} {j + 1}"
            for j, k in at.items()}
    lines = [f"{way}."]
    if not rows:
        lines.append('No ferrite drawn: nothing for it to bias.')
    for k, r in sorted(rows.items()):
        lines.append(f"{name.get(k, r['name'])}: {_ferrite_line(r, magnets)}")
    return '\n'.join(lines)
