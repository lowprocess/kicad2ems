"""
Field dumps: planes or volumes of E, H or current density, in the time or frequency domain.

Spec (command line --dump, repeatable, or `dump = [...]` in the TOML config):

    FIELD@REGION[@FREQS]

    FIELD   E | H | J        (J = total current density, rot H: shows the copper currents)
    REGION  F.Cu             plane at a copper layer's height
            F.Cu+0.05        ... offset in mm (+ up, - down), e.g. inside the dielectric under a trace
            z=0.3            plane at a height (mm, z = 0 at the bottom copper)
            x=12  /  y=7     vertical cut planes (KiCad mm, y down as in KiCad)
            volume           the whole board (outline x stack) plus 0.5 mm
            volume+5         ... with 5 mm of air above and below (fringing fields, an antenna's near field;
                             the GUI's 3D view)
            box=x0,y0,z0,x1,y1,z1   any box (KiCad mm, z as above)
    FREQS   2,4,6            GHz: frequency-domain dump (HDF5) at those frequencies, plotted to PNG
            (omitted)        time-domain dump (VTK series: the GUI's Fields tab, live, or ParaView)

Examples:  --dump E@F.Cu+0.05@2,6   --dump J@In2.Cu@4   --dump E@y=7   --dump H@volume@5

Planes are clipped to the board outline (+1 mm) and the stack (+1 mm air), so a dump does not cover
the empty air margin; `volume` is the board + 0.5 mm; a box is used as given.
"""
import json, os, re
import numpy as np

FIELDS = {'E': (0, 10), 'H': (1, 11), 'J': (3, 13)}                 # (time-domain, frequency-domain) types
UNITS = {'E': 'V/m', 'H': 'A/m', 'J': 'A/m²'}
G_DUMP = 'FDTD/Field dumps'                                        # the GUI's settings group of --dump


class Dump:
    def __init__(self, spec, field, start, stop, freqs, normal=None, label=''):
        self.spec, self.field, self.start, self.stop = spec, field, np.asarray(start, float), np.asarray(stop, float)
        self.freqs = freqs                                          # Hz list, or None for time domain
        self.normal = normal                                        # 0/1/2 for planes, None for volumes
        self.label = label
        tag = label.replace('+', 'p').replace('-', 'm').replace('=', '')
        safe = re.sub(r'[^A-Za-z0-9]+', '_', f"{field}_{tag}").strip('_')
        self.name = f"dump_{safe}_{'fd' if freqs else 'td'}"

    @property
    def domain(self):
        return 'FD' if self.freqs else 'TD'

    def cells(self, mesh):
        n = 1
        for k, ax in enumerate('xyz'):
            lines = mesh[ax]
            n *= max(1, int(np.sum((lines >= self.start[k] - 1e-9) & (lines <= self.stop[k] + 1e-9))))
        return n

    def describe(self, mesh):
        dom = (f"at {', '.join(f'{f / 1e9:g}' for f in self.freqs)} GHz (HDF5)" if self.freqs
               else 'time domain (VTK)')
        kind = f"plane {self.label}" if self.normal is not None else (self.label if self.label.startswith('volume') else f"volume {self.label}")
        return f"{self.name}: {self.field} {kind}, {self.cells(mesh):,} points, {dom}"


def parse_dump(spec, m, mesh=None):
    """Turn a dump spec into a Dump (model coordinates: mm, y up)."""
    parts = spec.split('@')
    if len(parts) not in (2, 3):
        raise ValueError(f"--dump {spec!r}: expected FIELD@REGION[@FREQS], e.g. E@F.Cu+0.05@2,6")
    field, region = parts[0].strip().upper(), parts[1].strip()
    if field not in FIELDS:
        raise ValueError(f"--dump {spec!r}: field must be one of {', '.join(FIELDS)}")
    freqs = None
    if len(parts) == 3 and parts[2].strip():
        try:
            freqs = [float(v) * 1e9 for v in parts[2].split(',') if v.strip()]
        except ValueError:
            raise ValueError(f"--dump {spec!r}: frequencies are GHz values separated by commas")
    ob = m.outline.bounds
    x0, y0, x1, y1 = ob[0] - 1, ob[1] - 1, ob[2] + 1, ob[3] + 1      # board + 1 mm
    z0, z1 = -1.0, m.thickness + 1.0
    if mesh is not None:                                            # never beyond the simulation domain
        x0, x1 = max(x0, mesh['x'][0]), min(x1, mesh['x'][-1])
        y0, y1 = max(y0, mesh['y'][0]), min(y1, mesh['y'][-1])
        z0, z1 = max(z0, mesh['z'][0]), min(z1, mesh['z'][-1])
    lay = re.fullmatch(r'([A-Za-z0-9_.]+?\.Cu)([+-][0-9.]+)?', region)
    if lay and lay.group(1) in m.cu_z:
        z = m.cu_z[lay.group(1)] + (float(lay.group(2)) if lay.group(2) else 0.0)
        return Dump(spec, field, (x0, y0, z), (x1, y1, z), freqs, 2, region)
    if lay:
        raise ValueError(f"--dump {spec!r}: no copper layer {lay.group(1)} (layers: {', '.join(m.cu_order)})")
    mm = re.fullmatch(r'([xyz])\s*=\s*(-?[0-9.]+)', region)
    if mm:
        ax, v = mm.group(1), float(mm.group(2))
        if ax == 'x':
            return Dump(spec, field, (v, y0, z0), (v, y1, z1), freqs, 0, region)
        if ax == 'y':
            v = -v                                                  # KiCad y (down) -> model y (up)
            return Dump(spec, field, (x0, v, z0), (x1, v, z1), freqs, 1, region)
        return Dump(spec, field, (x0, y0, v), (x1, y1, v), freqs, 2, region)
    vm = re.fullmatch(r'volume(?:\+([0-9.]+))?', region.lower())
    if vm:
        air = float(vm.group(1)) if vm.group(1) else 0.5            # volume+MM: MM of air above and below
        lo, hi = (max(z0, -air), min(z1, m.thickness + air)) if not vm.group(1) else (-air, m.thickness + air)
        if mesh is not None:                                        # volume+MM: as far as the simulation goes
            lo, hi = max(lo, mesh['z'][0]), min(hi, mesh['z'][-1])
        return Dump(spec, field, (ob[0] - .5, ob[1] - .5, lo), (ob[2] + .5, ob[3] + .5, hi),
                    freqs, None, 'volume' if not vm.group(1) else f"volume+{air:g}")
    bm = re.fullmatch(r'box\s*=\s*(.+)', region)
    if bm:
        v = [float(t) for t in bm.group(1).split(',')]
        if len(v) != 6:
            raise ValueError(f"--dump {spec!r}: box needs x0,y0,z0,x1,y1,z1")
        a = (min(v[0], v[3]), min(-v[1], -v[4]), min(v[2], v[5]))
        b = (max(v[0], v[3]), max(-v[1], -v[4]), max(v[2], v[5]))
        normal = next((k for k in range(3) if a[k] == b[k]), None)
        return Dump(spec, field, a, b, freqs, normal, 'box')
    raise ValueError(f"--dump {spec!r}: unknown region {region!r} (layer[+-mm], x=/y=/z=, volume, box=...)")


def add_dumps(CSX, dumps):
    """Add the dump boxes to a CSXCAD structure (plain Python numbers: older CSXCAD is strict)."""
    for d in dumps:
        td, fd = FIELDS[d.field]
        kw = dict(dump_type=int(fd if d.freqs else td), file_type=int(1 if d.freqs else 0), dump_mode=2)
        if d.freqs:
            kw['frequency'] = [float(f) for f in d.freqs]
        box = CSX.AddDump(d.name, **kw)
        box.AddBox([float(v) for v in d.start], [float(v) for v in d.stop])


def write_info(path, dumps, m, dt_ps=None):
    """<run>/dump_info.json, written before the run so a viewer can follow it live: the dumps, the
    timestep and the geometry to outline (board outline, copper per layer, ports; model mm, y up)."""
    def rings(g):
        g = g.simplify(0.01, preserve_topology=True)
        return [[[round(x, 4), round(y, 4)] for x, y in r.coords] for r in [g.exterior] + list(g.interiors)]
    info = dict(dt_ps=dt_ps, dumps=[dict(name=d.name, spec=d.spec, field=d.field, label=d.label, normal=d.normal,
                                         freqs=d.freqs, start=d.start.tolist(), stop=d.stop.tolist()) for d in dumps],
                outline=rings(m.outline)[0], cu_z={k: float(v) for k, v in m.cu_z.items()},
                copper={lay: [rings(p) for p in ps] for lay, ps in m.copper.items()},
                ports=[dict(name=p.name, at=[float(p.c[0]), float(p.c[1])]) for p in m.ports],
                vias=[[round(float(v), 4) for v in via] for via in m.vias],        # x, y, r, z0, z1 (the 3D view)
                thickness=float(m.thickness))
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'dump_info.json'), 'w') as fh:
        json.dump(info, fh)


def write_norm(path, norm):
    """<run>/dump_norm.json: the incident port voltage at each frequency-domain dump frequency."""
    with open(os.path.join(path, 'dump_norm.json'), 'w') as fh:
        json.dump([[float(f), float(np.real(v)), float(np.imag(v))] for f, v in norm.items()], fh)


def estimate_size_mb(dumps, mesh, n_td_frames=60):
    """Rough disk use: 3 float32 components per point; FD stores complex values per frequency."""
    tot = 0.0
    for d in dumps:
        n = d.cells(mesh) * 3 * 4
        tot += n * (2 * len(d.freqs) if d.freqs else n_td_frames)
    return tot / 1e6


# -------------------------------------------------------------------------------------------------
# reading and plotting frequency-domain HDF5 dumps
# -------------------------------------------------------------------------------------------------
def _to_nxyz3(a, order):
    """Field array -> (nx, ny, nz, 3) given its storage order ('NXYZ' new openEMS, 'NZYX' old)."""
    order = (order or 'NZYX').upper()
    if order == 'NXYZ':
        return np.transpose(a, (1, 2, 3, 0))
    if order == 'NZYX':
        return np.transpose(a, (3, 2, 1, 0))
    raise ValueError(f"unknown HDF5 field order {order}")


def read_fd(path):
    """openEMS HDF5 frequency-domain dump -> (mesh dict x/y/z in mm, list of (freq Hz, field (nx, ny, nz, 3)
    complex)). Handles the newer layout (complex dataset 'f<i>', attribute d_order, mesh in drawing
    units with mesh_scaling) and the older one ('f<i>_real' / 'f<i>_imag', z-y-x order, mesh in m)."""
    import h5py
    with h5py.File(path, 'r') as h:
        sc = h['Mesh'].attrs.get('mesh_scaling', None)
        mesh = {}
        for ax in 'xyz':
            v = np.asarray(h['Mesh'][ax], float)
            # openEMS writes metres (some versions: drawing units with mesh_scaling); boards are < 2 m
            mesh[ax] = v * 1e3 if np.abs(v).max() < 2.0 else v * (float(sc) * 1e3 if sc is not None else 1.0)
        fd = h['FieldData']['FD']
        keys = list(fd.keys())
        idx = sorted({int(re.match(r'f(\d+)', k).group(1)) for k in keys if re.match(r'f\d+', k)})
        freqs_attr = np.atleast_1d(fd.attrs.get('frequency', []))
        out = []
        for i in idx:
            if f'f{i}' in fd:                                       # newer: one complex dataset
                ds = fd[f'f{i}']
                data, order = np.asarray(ds), ds.attrs.get('d_order', 'NXYZ')
                f = float(np.atleast_1d(ds.attrs.get('frequency', [np.nan]))[0])
            else:                                                   # older: real / imaginary parts
                re_, im_ = fd[f'f{i}_real'], fd[f'f{i}_imag']
                data = np.asarray(re_) + 1j * np.asarray(im_)
                order = re_.attrs.get('d_order', 'NZYX')
                f = float(np.atleast_1d(re_.attrs.get('frequency', [np.nan]))[0])
            if isinstance(order, bytes):
                order = order.decode()
            if not np.isfinite(f) and i < len(freqs_attr):
                f = float(freqs_attr[i])
            out.append((f, _to_nxyz3(data, order)))
    return mesh, out


def plot_fd_dumps(d, path, m, out_prefix, norm=None, port='', log=print):
    """|field| maps of a frequency-domain plane dump, one PNG per frequency, copper outlined. norm:
    {freq: incident port voltage} - fields are then scaled to a 1 V incident wave at that port."""
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.colors import LogNorm
    mesh, frames = read_fd(path)
    files = []
    if d.normal is None:
        return files
    ax_u, ax_v = [k for k in range(3) if k != d.normal]
    names = 'xyz'
    for f, data in frames:
        mag = np.sqrt(np.sum(np.abs(data) ** 2, axis=-1))           # |F| from the three components
        scale = None
        if norm:
            key = min(norm, key=lambda q: abs(q - f))
            if abs(key - f) < 1e-3 * f and abs(norm[key]) > 0:
                scale = 1.0 / abs(norm[key])
                mag = mag * scale
        sl = [slice(None)] * 3
        sl[d.normal] = 0
        img = mag[tuple(sl)]
        u, v = mesh[names[ax_u]], mesh[names[ax_v]]
        fig = Figure(figsize=(10, 5.2))
        ax = fig.subplots()
        vmax = np.nanmax(img) if np.isfinite(img).any() and np.nanmax(img) > 0 else 1.0
        base = matplotlib.colormaps['inferno']
        cmap = base.with_extremes(bad=base(0.0))                    # zero field (inside metal) stays dark
        im = ax.pcolormesh(u, -v if names[ax_v] == 'y' else v, np.ma.masked_less_equal(img.T, 0), shading='nearest',
                           cmap=cmap, norm=LogNorm(vmin=vmax * 1e-3, vmax=vmax))
        if d.normal == 2:                                           # copper outlines on and near this plane
            z = d.start[2]
            for lay, ps in m.copper.items():
                if abs(m.cu_z[lay] - z) < 0.3:
                    for p in ps:
                        for ring in [p.exterior] + list(p.interiors):
                            xs, ys = ring.xy
                            ax.plot(xs, [-y for y in ys], color='#7fd1ff', lw=0.6, alpha=0.8)
            for p in m.ports:
                ax.plot(p.c[0], -p.c[1], 'o', color='#7fd1ff', ms=4)
                ax.annotate(p.name, (p.c[0], -p.c[1]), xytext=(4, 4), textcoords='offset points', color='#7fd1ff', fontsize=8)
            ax.invert_yaxis()
            ax.set_aspect('equal')
        elif names[ax_v] == 'z':
            ax.set_aspect('auto')
        ax.set_xlabel(f"{names[ax_u]} (mm{', KiCad' if names[ax_u] == 'y' else ''})")
        ax.set_ylabel(f"{names[ax_v]} (mm{', KiCad' if names[ax_v] == 'y' else ''})")
        fig.colorbar(im, ax=ax, label=f"|{d.field}| ({UNITS[d.field]}" +
                     (f", 1 V incident at {port})" if scale else ", relative)"))
        ax.set_title(f"|{d.field}| at {d.label}, {f / 1e9:g} GHz" + (f", {port} driven" if port else ''))
        fig.tight_layout()
        fn = f"{out_prefix}_{d.name}_{f / 1e9:g}GHz.png"
        fig.savefig(fn, dpi=130)
        files.append(fn)
    return files
