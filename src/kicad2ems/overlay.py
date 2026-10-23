"""
The line model as an overlay for the GUI's viewer (no Qt here: testable without a display). A preview or a run
writes <name>_lines.json (LineModel.drawing): every section's points (model mm, y up), its layer's height and
its Z0, eeff, length and delay; each path as the ids of its sections.

    d = load_lines(out_dir)                 # the newest <name>_lines.json of a folder, or None
    rgb = z0_colour(z0, ref, span)          # blue below the port impedance, red above
    i = nearest(d['elements'], x, y, tol)   # the section under the mouse (model mm), or None
    describe(e)                             # one line: label, Z0, eeff, length, delay
"""
import glob, json, os
import numpy as np

LOW, MID, HIGH = (0.16, 0.38, 0.85), (0.55, 0.55, 0.55), (0.85, 0.22, 0.13)   # Z0 below / at / above the port's


def load_lines(out_dir):
    """The newest <name>_lines.json of a results folder (a preview writes it), or None."""
    files = sorted(glob.glob(os.path.join(out_dir, '*_lines.json')), key=os.path.getmtime) if out_dir else []
    for p in reversed(files):
        try:
            with open(p) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get('format') == 'kicad2ems-lines':
            d['path'] = p
            return d
    return None


def reference(d):
    """The impedance the colours are centred on: the ports' (usually 50 ohm)."""
    zs = [z for z in (d.get('port_z0') or {}).values() if z]
    return float(np.median(zs)) if zs else 50.0


def span(d, ref=None):
    """Half the colour range in ohm: the largest departure of a section from `ref`, at least 10 % of it. With
    d['robust'] (a whole board's map, where a few power shapes and big pads sit far off): the 95th percentile of
    the traces' departures, so that the traces use the colours (the outliers saturate)."""
    ref = reference(d) if ref is None else ref
    els = d.get('elements', [])
    if d.get('relative'):                                   # a fraction of each section's target
        dev = [abs(v) for v in (deviation(e, ref) for e in els if e.get('kind') == 'trace') if v is not None]
        return max(float(np.percentile(dev, 95)), 0.1) if dev else 0.1
    if d.get('robust'):
        dev = [abs(e['z0'] - ref) for e in els if e.get('z0') and e.get('kind') == 'trace']
        if dev:
            return max(float(np.percentile(dev, 95)), 0.1 * ref)
    zs = [e['z0'] for e in els if e.get('z0')]
    return max([abs(z - ref) for z in zs] + [0.1 * ref])


def against_target(e, ref=50.0):
    """(value, target ohm, what the value is) a section is judged by: against a differential target, Zdiff of the
    coupled legs (2 Z0 where they run apart); otherwise Z0, against its single-ended target or `ref`."""
    z0, t = e.get('z0'), e.get('target')
    if t and e.get('tkind') == 'diff':
        return (e['zdiff'], t, 'Zdiff') if e.get('zdiff') else ((2 * z0 if z0 else None), t, '2 Z0 (legs apart)')
    return z0, t or ref, 'Z0'


def deviation(e, ref=50.0):
    """(value - target) / target of a section, or None (no impedance)."""
    v, t, _ = against_target(e, ref)
    return None if v is None or not t else (v - t) / t


def section_colour(e, d, ref, half):
    """A section's colour: by its deviation from its target in a map with targets (d['relative']; half: a
    fraction), else by its Z0 against ref (half: ohm)."""
    if d.get('relative'):
        return z0_colour(deviation(e, ref), 0.0, half)
    return z0_colour(e.get('z0'), ref, half)


def z0_colour(z0, ref=50.0, half=10.0):
    """RGB (0..1) of a section's Z0: grey at `ref`, blue `half` ohm below, red `half` ohm above; grey for None."""
    if z0 is None or not np.isfinite(z0):
        return MID
    t = float(np.clip((z0 - ref) / half, -1, 1))
    end = HIGH if t > 0 else LOW
    a = abs(t)
    return tuple(MID[k] + a * (end[k] - MID[k]) for k in range(3))


def _seg_dist(p, a, b):
    """Distance from p to the segment a-b (2D)."""
    ab = b - a
    L2 = float(ab @ ab)
    t = 0.0 if L2 == 0 else float(np.clip((p - a) @ ab / L2, 0, 1))
    return float(np.hypot(*(p - (a + t * ab))))


def distance(e, x, y):
    """Distance (mm) from a point to a section's centre line."""
    pts = np.asarray(e['pts'], float)
    p = np.array([x, y], float)
    if len(pts) == 1:
        return float(np.hypot(*(p - pts[0])))
    return min(_seg_dist(p, pts[k], pts[k + 1]) for k in range(len(pts) - 1))


def drawn(e):
    """Is a section drawn (and picked)? Not a coupling, nor a trace of no length: a stub that lies wholly inside a
    pad (routers leave them, 0.01 mm long) is the pad's, and drawn over it, it hid it."""
    return e.get('kind') != 'coupling' and not (e.get('kind') == 'trace' and not e.get('length'))


def nearest(elements, x, y, tol, layer=None):
    """Id of the section whose centre line is nearest to (x, y), within tol mm plus its half width; None if none
    is. layer: only that layer's sections."""
    best, bid = np.inf, None
    for e in elements:
        if (layer and e.get('layer') != layer) or not drawn(e):
            continue
        d = distance(e, x, y) - 0.5 * (e.get('w') or 0.0)
        if d < best and d <= tol:
            best, bid = d, e['id']
    return bid


def describe(e):
    """One line about a section: what it is, Z0, eeff, length and delay."""
    parts = [e.get('label') or e.get('kind', '')]
    if e.get('value'):
        parts.append(str(e['value']))
    if e.get('z0'):
        parts.append(f"Z0 {e['z0']:.1f} Ω")
    if e.get('netclass') is not None:
        if e.get('target'):
            v, t, what = against_target(e)
            dev = f", {what} {v:.1f} Ω ({(v - t) / t * 100:+.0f} %)" if v else ''
            parts.append(f"class {e['netclass']}: {t:g} Ω {'diff' if e.get('tkind') == 'diff' else 'SE'}{dev}")
        else:
            parts.append(f"class {e['netclass']}")
    if e.get('eeff'):
        parts.append(f"εeff {e['eeff']:.2f}")
    if e.get('length') is not None:
        parts.append(f"{e['length']:.2f} mm")
    if e.get('delay_ps'):
        parts.append(f"{e['delay_ps']:.1f} ps")
    if e.get('layer'):
        parts.append(e['layer'])
    return ' · '.join(parts)


def section_polygon(e):
    """A section's copper as a shapely geometry (model mm): a pad its own outline; otherwise its centre line widened
    by its width (flat ends, mitred corners); a via or a pad without length a disc; a part without width a thin
    strip."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
    if e.get('outline'):
        return unary_union([Polygon(r) for r in e['outline'] if len(r) >= 3])
    pts = np.asarray(e['pts'], float)
    w = e.get('w') or 0.0
    if len(pts) == 1 or np.allclose(pts[0], pts[-1]):
        return Point(pts[0]).buffer(max(w, 0.3) / 2, quad_segs=8)
    if w > 0:
        return LineString(pts).buffer(w / 2, cap_style='flat', join_style='mitre')
    return LineString(pts).buffer(0.08, cap_style='flat')


def relief(z0, lo, hi, floor=0.25):
    """A section's height in the 3D view as a fraction of the full relief: `floor` at the lowest Z0 of the model
    (lo), 1 at the highest (hi), so a step in Z0 is a step in height; `floor` for a section without a Z0 (a part)."""
    if z0 is None or not np.isfinite(z0) or hi is None or lo is None:
        return floor
    if hi - lo < 1e-9:
        return 0.5 * (1 + floor)
    return floor + (1 - floor) * float(np.clip((z0 - lo) / (hi - lo), 0, 1))


def plan_segments(path, box, z):
    """A solver's mesh (meshes.py file) seen from above on the copper plane at height z: its line segments
    (2 k, 2) in that plane, model mm. box: the board's (xmin, ymin, xmax, ymax, z0, z1)."""
    from . import meshes
    seg = meshes.segments(path, box=box, planes=[z])
    if not len(seg):
        return np.zeros((0, 2))
    a, b = seg[0::2], seg[1::2]
    keep = (np.abs(a[:, 2] - z) < 1e-6) & (np.abs(b[:, 2] - z) < 1e-6)
    return np.stack([a[keep, :2], b[keep, :2]], 1).reshape(-1, 2)


def layers(d):
    """The copper layers the sections are on, top first (by height)."""
    seen = {}
    for e in d.get('elements', []):
        if e.get('layer') and e.get('z') is not None:
            seen[e['layer']] = e['z']
    return sorted(seen, key=lambda l: -seen[l])
