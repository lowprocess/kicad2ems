"""
Field-dump viewing: the dumps of a run folder as 2D frames, for the GUI's field view and for animations.

    srcs = find_dumps(out_dir)              # every dump under <out>/<solver>/exc_*/ (openems, mom25d)
    src = srcs[0]; src.refresh()            # time domain: the .vtr frames written so far (a run may be going)
    mesh, data = src.load(i)                # (x/y/z mm, model axes), (nx, ny, nz, 3); complex for FD
    sl = plane(mesh, data, axis, index, component='mag', phase=None)
    save_animation(src, 'out.gif', ...)     # .gif (Pillow) or .mp4 (ffmpeg)

Time-domain dumps are VTK rectilinear grids (one .vtr per frame, zlib-compressed binary as openEMS
writes them), read here without VTK. Frequency-domain dumps are openEMS HDF5 files (dumps.read_fd) or
mom25d's surface currents on a grid per layer (.npz: x, y, z, freqs, data (nf, nx, ny, nz, 3) complex);
they are animated over the phase: Re(F e^(j phi)), one period at one frequency: the standing waves. A plane dump has one point along its normal; a volume
is cut at any x / y / z mesh line. 2D views are in KiCad axes (y down), components too (Fy = -Fy model).
"""
import base64, glob, json, os, re, shutil, subprocess, zlib
import xml.etree.ElementTree as ET
import numpy as np

from .dumps import UNITS

AXES = 'xyz'


# ---- time-domain VTR frames ----------------------------------------------------------------------
def _decode_binary(text, header_type, compressed, dtype):
    """One inline binary VTK DataArray (base64) -> numpy array."""
    text = ''.join(text.split())
    hdt = np.dtype('<u8' if header_type == 'UInt64' else '<u4')
    hs = hdt.itemsize
    if not compressed:
        raw = base64.b64decode(text)
        n = int(np.frombuffer(raw[:hs], hdt)[0])
        return np.frombuffer(raw[hs:hs + n], dtype)
    first = base64.b64decode(text[:((hs + 2) // 3) * 4])           # the block count leads the header
    nb = int(np.frombuffer(first[:hs], hdt)[0])
    hlen = ((hs * (3 + nb) + 2) // 3) * 4                          # header and data are encoded separately
    head = np.frombuffer(base64.b64decode(text[:hlen])[:hs * (3 + nb)], hdt)
    data = base64.b64decode(text[hlen:])
    out, pos = [], 0
    for c in head[3:]:
        out.append(zlib.decompress(data[pos:pos + int(c)]))
        pos += int(c)
    return np.frombuffer(b''.join(out), dtype)


_VTK_TYPES = {'Float32': '<f4', 'Float64': '<f8', 'Int32': '<i4', 'UInt32': '<u4'}


def read_vtr(path):
    """openEMS time-domain dump frame (.vtr) -> (mesh {x, y, z} mm, field (nx, ny, nz, 3) float32)."""
    root = ET.parse(path).getroot()
    ht = root.get('header_type', 'UInt32')
    comp = root.get('compressor') is not None

    def arr(el):
        dt = np.dtype(_VTK_TYPES[el.get('type')])
        fmt = el.get('format', 'ascii')
        if fmt == 'ascii':
            return np.array(el.text.split(), dt)
        if fmt == 'binary':
            return _decode_binary(el.text or '', ht, comp, dt)
        raise ValueError(f"{path}: VTK data format {fmt!r} is not supported")

    piece = root.find('RectilinearGrid/Piece')
    coords = [arr(el) for el in piece.find('Coordinates').findall('DataArray')]
    # openEMS writes metres (drawing units scaled); boards are < 2 m
    mesh = {ax: (c * 1e3 if np.abs(c).max() < 2.0 else c).astype(float) for ax, c in zip(AXES, coords)}
    el = piece.find('PointData/DataArray')
    nc = int(el.get('NumberOfComponents', 1))
    v = arr(el).astype(np.float32).reshape(len(coords[2]), len(coords[1]), len(coords[0]), nc)  # x fastest
    v = np.transpose(v, (2, 1, 0, 3))
    if nc == 1:
        v = np.repeat(v, 3, axis=-1) * np.array([1, 0, 0], np.float32)
    return mesh, v


# ---- the dumps of a run folder -------------------------------------------------------------------
def _info(run_path):
    try:
        with open(os.path.join(run_path, 'dump_info.json')) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _dt_ps(run_path, info):
    """openEMS's timestep: its log, else the mesh estimate in dump_info.json."""
    try:
        with open(os.path.join(run_path, 'openems.log'), errors='replace') as fh:
            m = re.search(r'FDTD timestep is:\s*([0-9.eE+-]+)\s*s', fh.read(200_000))
        if m:
            return float(m.group(1)) * 1e12
    except OSError:
        pass
    return info.get('dt_ps')


class DumpSource:
    """One field dump of one run: time domain (a growing series of .vtr frames) or frequency domain (one
    HDF5 file, frames = frequencies)."""
    def __init__(self, run_path, name, domain):
        self.path, self.name, self.domain = run_path, name, domain
        self.run = os.path.basename(run_path).replace('exc_', '', 1)
        self.solver = os.path.basename(os.path.dirname(run_path))   # openems, mom25d
        info = _info(run_path)
        self.geometry = info
        self.meta = next((d for d in info.get('dumps', []) if d['name'] == name), {})
        self.field = self.meta.get('field') or (re.match(r'dump_([EHJ])_', name) or [None, 'E'])[1]
        self.unit = self.meta.get('unit') or UNITS.get(self.field, '')
        self.frames, self.freqs, self._cache, self._fd = [], [], {}, None
        self.norm = {}
        self.dt_ps = None
        self.refresh()

    @property
    def label(self):
        lab = self.meta.get('label') or self.name
        who = '' if self.solver == 'openems' else f"{self.solver} "
        return f"{who}{self.run}: {self.field} {lab} ({'time' if self.domain == 'TD' else 'frequency'} domain)"

    def refresh(self):
        """Pick up new frames (time domain: during a run, the newest file may still be being written)."""
        if self.domain == 'TD':
            fs = glob.glob(os.path.join(self.path, self.name + '_*.vtr'))
            self.frames = sorted(((int(re.search(r'_(\d+)\.vtr$', f).group(1)), f) for f in fs
                                  if re.search(r'_(\d+)\.vtr$', f)))
            if self.dt_ps is None:
                self.dt_ps = _dt_ps(self.path, self.geometry)
        else:
            h5, npz = (os.path.join(self.path, self.name + ext) for ext in ('.h5', '.npz'))
            if os.path.exists(h5) and self._fd is None:
                try:
                    from .dumps import read_fd
                    self._fd = read_fd(h5)
                    self.freqs = [f for f, _ in self._fd[1]]
                except (OSError, KeyError):                         # still being written
                    self._fd = None
            elif os.path.exists(npz) and self._fd is None:
                self._fd = read_npz(npz)
                self.freqs = [f for f, _ in self._fd[1]]
            try:
                with open(os.path.join(self.path, 'dump_norm.json')) as fh:
                    self.norm = {f: complex(a, b) for f, a, b in json.load(fh)}
            except (OSError, ValueError):
                self.norm = {}
        return self

    def __len__(self):
        return len(self.frames) if self.domain == 'TD' else len(self.freqs)

    def frame_label(self, i, phase=None):
        if self.domain == 'TD':
            step = self.frames[i][0]
            return f"step {step:,}" + (f", t = {step * self.dt_ps / 1e3:.3f} ns" if self.dt_ps else '')
        return f"{self.freqs[i] / 1e9:g} GHz" + (f", phase {np.degrees(phase) % 360:.0f}°" if phase is not None else '')

    def scale(self, i):
        """Frequency domain: factor to a 1 V incident wave at the driven port (None if unknown)."""
        if self.domain == 'TD' or not self.norm:
            return None
        f = self.freqs[i]
        key = min(self.norm, key=lambda q: abs(q - f))
        v = self.norm[key]
        return 1.0 / abs(v) if abs(key - f) < 1e-3 * f and abs(v) > 0 else None

    def load(self, i):
        """(mesh, field (nx, ny, nz, 3)) of frame i (cached; float32 / complex)."""
        if self.domain == 'FD':
            mesh, frames = self._fd
            data = frames[i][1]
            s = self.scale(i)
            return mesh, (data * s if s else data)
        path = self.frames[i][1]
        if path not in self._cache:
            if len(self._cache) > 400:
                self._cache.pop(next(iter(self._cache)))
            self._cache[path] = read_vtr(path)
        return self._cache[path]

    def newest_complete(self):
        """Index of the newest frame that reads (a live run may be writing the last one), or None."""
        for i in range(len(self.frames) - 1, max(-1, len(self.frames) - 4), -1):
            try:
                self.load(i)
                return i
            except Exception:                                       # partial file: try the one before
                self._cache.pop(self.frames[i][1], None)
        return None

    def shape(self):
        """Mesh line counts (nx, ny, nz) of the dump, from its first frame."""
        mesh, _ = self.load(0)
        return tuple(len(mesh[a]) for a in AXES)


def read_npz(path):
    """A frequency-domain dump as a grid (mom25d's currents): (mesh x/y/z mm, [(freq Hz, (nx, ny, nz, 3))])."""
    with np.load(path) as z:
        mesh = {a: np.asarray(z[a], float) for a in AXES}
        return mesh, [(float(f), d) for f, d in zip(z['freqs'], z['data'])]


def write_npz(path, mesh, freqs, data):
    """The counterpart of read_npz: data (nf, nx, ny, nz, 3) complex."""
    np.savez_compressed(path, freqs=np.asarray(freqs, float), data=np.asarray(data, np.complex64),
                        **{a: np.asarray(mesh[a], float) for a in AXES})


def find_dumps(out_dir):
    """Every field dump in <out>/<solver>/exc_*/, openEMS's first (the dump_info.json names them before any
    file exists)."""
    out = []
    runs = sorted(glob.glob(os.path.join(out_dir, '*', 'exc_*')),
                  key=lambda r: (os.path.basename(os.path.dirname(r)) != 'openems', r))
    for run in runs:
        names = {}
        for d in _info(run).get('dumps', []):
            names[d['name']] = 'FD' if d.get('freqs') else 'TD'
        for f in glob.glob(os.path.join(run, 'dump_*')):
            b = os.path.basename(f)
            m = re.match(r'(dump_.+_td)_\d+\.vtr$', b) or re.match(r'(dump_.+_fd)\.(h5|npz)$', b)
            if m:
                names.setdefault(m.group(1), 'TD' if b.endswith('.vtr') else 'FD')
        out += [DumpSource(run, n, dom) for n, dom in sorted(names.items())]
    return out


# ---- 2D views ------------------------------------------------------------------------------------
class Plane:
    """A 2D view: u, v axis coordinates (ascending, mm, KiCad axes), img[u, v], axis names, title."""
    def __init__(self, u, v, img, names, axis, pos, signed):
        self.u, self.v, self.img, self.names, self.axis, self.pos, self.signed = u, v, img, names, axis, pos, signed

    def resample(self, n=900):
        """img on a uniform grid (nearest mesh line), for display as an image: (img, (u0, v0, du, dv))."""
        if len(self.u) < 2 or len(self.v) < 2:
            return self.img, (self.u[0], self.v[0], 1.0, 1.0)
        def grid(c):
            k = int(min(n, max(2, np.ceil((c[-1] - c[0]) / max(np.min(np.diff(c)), 1e-9)) + 1)))
            g = np.linspace(c[0], c[-1], k)
            j = np.clip(np.searchsorted(c, g), 1, len(c) - 1)
            return g, np.where(g - c[j - 1] <= c[j] - g, j - 1, j)
        gu, iu = grid(self.u)
        gv, iv = grid(self.v)
        return self.img[np.ix_(iu, iv)], (gu[0], gv[0], gu[1] - gu[0], gv[1] - gv[0])


def plane(mesh, data, axis, index, component='mag', phase=None):
    """Cut a field (nx, ny, nz, 3) at mesh line `index` normal to `axis` (0/1/2) -> Plane. component: 'mag'
    or 'x'/'y'/'z' (KiCad axes); complex (FD) data: phase None -> |F| (peak), else Re(F e^(j phase))."""
    sl = [slice(None)] * 3
    sl[axis] = min(index, data.shape[axis] - 1)
    f = data[tuple(sl)]                                             # (nu, nv, 3)
    if np.iscomplexobj(f):
        if phase is None:
            vec, peak = np.abs(f), True
        else:
            vec, peak = np.real(f * np.exp(1j * phase)), False
    else:
        vec, peak = f, False
    if not peak:
        vec = vec * np.array([1, -1, 1], vec.dtype)                 # model y up -> KiCad y down
    signed = component != 'mag' and not peak
    img = np.sqrt(np.sum(np.abs(vec) ** 2, axis=-1)) if component == 'mag' else vec[..., AXES.index(component)]
    ua, va = [k for k in range(3) if k != axis]
    u, v = np.asarray(mesh[AXES[ua]], float), np.asarray(mesh[AXES[va]], float)
    if ua == 1:                                                     # y axes in KiCad coordinates
        u, img = -u[::-1], img[::-1, :]
    if va == 1:
        v, img = -v[::-1], img[:, ::-1]
    return Plane(u, v, np.asarray(img, float), (AXES[ua], AXES[va]), axis, float(mesh[AXES[axis]][sl[axis]]), signed)


def default_axis(shape):
    """The normal of a plane dump (its single-point axis), else z."""
    return next((k for k in range(3) if shape[k] == 1), 2)


def outlines(geometry, axis, pos, near=0.3):
    """Copper outlines in a view (KiCad mm): one (us, vs) polyline array pair per ring or cut segment.
    z planes: the copper of layers within `near` mm; x / y cuts: copper crossing the cut, at its height."""
    lines = []
    if not geometry:
        return lines
    cu_z, copper = geometry.get('cu_z', {}), geometry.get('copper', {})
    if axis == 2:
        for lay, polys in copper.items():
            if abs(cu_z.get(lay, 1e9) - pos) < near:
                for rings in polys:
                    for r in rings:
                        a = np.asarray(r, float)
                        lines.append((a[:, 0], -a[:, 1]))
        ol = np.asarray(geometry.get('outline') or [], float)
        if len(ol):
            lines.append((ol[:, 0], -ol[:, 1]))
        return lines
    from shapely.geometry import LineString, Polygon
    big = 1e4
    cut = LineString([(pos, -big), (pos, big)] if axis == 0 else [(-big, pos), (big, pos)])
    for lay, polys in copper.items():
        z = cu_z.get(lay)
        if z is None:
            continue
        for rings in polys:
            g = Polygon(rings[0], rings[1:]).intersection(cut)
            for seg in getattr(g, 'geoms', [g]):
                if seg.is_empty or seg.geom_type != 'LineString':
                    continue
                c = np.asarray(seg.coords)
                h = -c[:, 1] if axis == 0 else c[:, 0]              # x cut: horizontal = KiCad y
                lines.append((np.array([h.min(), h.max()]), np.array([z, z])))
    return lines


def levels(values, signed, log, decades=3):
    """Colour range (lo, hi) of a set of images: symmetric for signed components, `decades` below the
    maximum for log magnitude."""
    vmax = max((float(np.nanmax(np.abs(v))) for v in values if v.size and np.isfinite(v).any()), default=0.0) or 1.0
    if signed:
        return -vmax, vmax
    return (vmax * 10 ** -decades, vmax) if log else (0.0, vmax)


# ---- animations ----------------------------------------------------------------------------------
def ffmpeg_exe():
    """The ffmpeg program: on PATH, or the one of the imageio-ffmpeg package; None if neither."""
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def render(pl, lo, hi, log, title, geometry=None, cbar='', size=(9, 5), dpi=110):
    """One view as an RGB image (h, w, 3) uint8, drawn with matplotlib (copper outlined)."""
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import LogNorm, Normalize
    fig = Figure(figsize=size, dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    if pl.signed:
        cmap, norm = matplotlib.colormaps['RdBu_r'], Normalize(lo, hi)
        img = pl.img
    else:
        base = matplotlib.colormaps['inferno']
        cmap = base.with_extremes(bad=base(0.0), under=base(0.0))
        norm = LogNorm(lo, hi) if log else Normalize(lo, hi)
        img = np.ma.masked_less_equal(pl.img, 0) if log else pl.img
    im = ax.pcolormesh(pl.u, pl.v, img.T, shading='nearest', cmap=cmap, norm=norm)
    for us, vs in outlines(geometry, pl.axis, pl.pos):
        ax.plot(us, vs, color='#7fd1ff' if not pl.signed else '#333333', lw=0.6 if pl.axis == 2 else 2.0, alpha=0.85)
    ax.set_xlim(pl.u[0], pl.u[-1])
    ax.set_ylim(pl.v[0], pl.v[-1])
    if pl.axis == 2:
        ax.invert_yaxis()
        ax.set_aspect('equal')
    ax.set_xlabel(f"{pl.names[0]} (mm)")
    ax.set_ylabel(f"{pl.names[1]} (mm)")
    fig.colorbar(im, ax=ax, label=cbar, extend='min' if log and not pl.signed else 'neither')
    ax.set_title(title)
    fig.tight_layout()
    fig.canvas.draw()
    return np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()


def frame_views(src, axis, index, component, frames=None, n_phase=24, log=True):
    """The views of an animation: time domain -> each frame (default all); frequency domain -> one
    period of Re(F e^(j phase)) at frame frames[0]. Returns [(Plane, label)] and the colour range."""
    views = []
    if src.domain == 'TD':
        for i in (range(len(src)) if frames is None else frames):
            mesh, data = src.load(i)
            views.append((plane(mesh, data, axis, index, component), src.frame_label(i)))
    else:
        i = (frames or [0])[0]
        mesh, data = src.load(i)
        for ph in np.linspace(0, 2 * np.pi, n_phase, endpoint=False):
            views.append((plane(mesh, data, axis, index, component, phase=ph), src.frame_label(i, ph)))
    if not views:
        raise ValueError(f"{src.name}: no frames to animate")
    lo, hi = levels([p.img for p, _ in views], views[0][0].signed, log)
    return views, (lo, hi)


def save_animation(src, path, axis, index, component='mag', frames=None, fps=12, log=True, n_phase=24,
                   lim=None, progress=None):
    """Write an animation of a dump cut (see frame_views) to .gif (Pillow) or .mp4 (ffmpeg, H.264), one
    colour range for all frames (lim = (lo, hi), else from all the frames). progress(i, n) is called per
    frame; returns the path."""
    views, (lo, hi) = frame_views(src, axis, index, component, frames, n_phase, log and component == 'mag')
    if lim:
        lo, hi = lim
    use_log = log and not views[0][0].signed
    scale = src.scale((frames or [0])[0]) if src.domain == 'FD' else None
    what = f"|{src.field}|" if component == 'mag' else f"{src.field}_{component}"
    cbar = f"{what} ({src.unit}" + (f", 1 V incident at {src.run})" if scale else ")")
    where = f"{AXES[axis]} = {views[0][0].pos if axis != 1 else -views[0][0].pos:.3f} mm"
    imgs = []
    for k, (pl, lab) in enumerate(views):
        imgs.append(render(pl, lo, hi, use_log, f"{what}, {where}, {lab} ({src.run} driven)", src.geometry, cbar))
        if progress:
            progress(k + 1, len(views))
    return write_frames(imgs, path, fps)


def write_frames(imgs, path, fps=12):
    """RGB frames ((h, w, 3) uint8, one size) to .gif (Pillow) or .mp4 (ffmpeg, H.264); returns the path."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.gif':
        from PIL import Image
        ims = [Image.fromarray(a) for a in imgs]
        ims[0].save(path, save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0, optimize=False)
    elif ext == '.mp4':
        exe = ffmpeg_exe()
        if not exe:
            raise RuntimeError("saving .mp4 needs ffmpeg (install it, or pip install imageio-ffmpeg); .gif works without")
        h, w, _ = imgs[0].shape
        cmd = [exe, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f"{w}x{h}", '-r', str(fps),
               '-i', '-', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-pix_fmt', 'yuv420p', '-vcodec', 'libx264', path]
        p = subprocess.run(cmd, input=b''.join(a.tobytes() for a in imgs), capture_output=True)
        if p.returncode:
            raise RuntimeError(f"ffmpeg failed: {p.stderr.decode(errors='replace').strip()[-400:]}")
    else:
        raise ValueError(f"{path}: save as .gif or .mp4")
    return path


def save_fd_outputs(src, prefix, animate=None, log=True, n_phase=24):
    """The files of a frequency-domain plane dump, per frequency: <prefix>_<GHz>GHz.png (the peak |F|) and, with
    animate = 'gif' / 'mp4', <prefix>_<GHz>GHz.<animate>: one period of Re(F e^(j phase)), the standing waves.
    Returns [(freq Hz, path)]."""
    out = []
    axis = default_axis(src.shape())
    what = f"|{src.field}|"
    for i, f in enumerate(src.freqs):
        mesh, data = src.load(i)
        pl = plane(mesh, data, axis, 0)
        lo, hi = levels([pl.img], False, log)
        cbar = f"{what} ({src.unit}" + (", 1 V incident)" if src.scale(i) else ")")
        img = render(pl, lo, hi, log, f"{what} (peak), {src.meta.get('label', '')}, {f / 1e9:g} GHz ({src.run} driven)",
                     src.geometry, cbar)
        from PIL import Image
        png = f"{prefix}_{f / 1e9:g}GHz.png"
        Image.fromarray(img).save(png)
        out.append((f, png))
        if animate:
            out.append((f, save_animation(src, f"{prefix}_{f / 1e9:g}GHz.{animate}", axis, 0, 'mag', [i], log=log,
                                          n_phase=n_phase)))
    return out
