"""
mom25d.sim - the general simulation object (stage 3)

    sim = Simulation(stack, shapes, ports=[...], vias=[...], lumped=[...],
                     dmax=...)
    res = sim.sweep(freqs)

shapes  : {interface: [rect (x0,y0,x1,y1) | shapely Polygon/MultiPolygon, ...]}
ports   : LinePort (wave port, de-embedded to a reference plane, any direction)
          GapPort  (lumped port across a strip, reference plane at the gap)
          ViaPort  (lumped port in a via, e.g. pad-to-ground)
vias    : Via(x, y, k_bot, k_top, radius)   k = 0 the bottom ground (an open
bottom: metal there), k = N a PEC cover lumped  : SeriesLumped (element across a
strip); Via(..., Z=...) puts an element inside a via metal   : Metal(sigma, t,
rough) -> surface impedance on strips, wall loss on vias (optional)

Unknowns: RWG edges on all metal layers, then one per via chain. Via-related
matrix blocks use the kernels from mom25d.via (validated against exact image
solutions); the via end charges are fan-distributed and enter through the same
G_q interaction as the strip charges.
"""
from types import SimpleNamespace

import os
import numpy as np
import shapely
# (bytes; mom25d's name for it)
from kicad2ems.emsdev.memory import available as available_memory  # noqa: F401

from .model import KNOWN_SPAN
from kicad2ems.emsdev.numerics.base import (MU0, RWG, quad_pts, _BAR3, _W3,
                                            _BAR6, _W6)
from kicad2ems.emsdev.numerics.geo import to_geometry, is_manhattan_rects, ngon
from .base import fit_gamma, fit_waves
from .multilayer import RunLog, GreenTableML, make_mesh_ml
from .geo import LinePort, mesh_polygons
from .via import ViaKernels

# a port's cut, a lumped element's line: its ends moved to the copper's edge
# within this x its width (Simulation._snap)
SNAP = 0.02
# rad: a wave port's cuts must span this much to fit gamma (a port without a
# characterised one)
FIT_SPAN = 0.4
# balance: ports coupled by less than this (|S_ij S_ji|, -20 dB each way) keep
# their scales
BALANCE_FLOOR = 1e-2


# ---- Components -------------------------------------------------------------
def _ls(layer):
    return list(layer) if isinstance(layer, tuple) else [layer]


def _zfun(Z):
    return Z if callable(Z) else (lambda f, Z=Z: complex(Z))


def rlc(R=0.0, L=0.0, C=None, parallel=False):
    """Impedance function of a series (default) or parallel R-L-C element."""
    def Z(f):
        w = 2 * np.pi * f
        if not parallel:
            return R + 1j * w * L + (1 / (1j * w * C) if C else 0)
        return (1 / ((1 / R if R else 0) + (1 / (1j * w * L) if L else 0) +
                     (1j * w * C if C else 0)))
    return Z


# an opening's cells (slots.py) in dmax away from the copper, as a plane's
SLOT_FACTOR = 3.0


class Via:
    """Plated via: axis (x, y), from interface k_bot to k_top, barrel radius. Z:
    optional series element (callable f -> ohms) placed in the lowest chain
    (e.g. a chip part to ground)."""
    def __init__(self, x, y, k_bot, k_top, radius=0.15e-3, Z=None, name='',
                 ring=True):
        self.x, self.y, self.k_bot, self.k_top, self.a, self.name = (x, y,
                                                                     k_bot,
                                                                     k_top,
                                                                     radius,
                                                                     name)
        # refine the mesh with an octagon of the via radius around it
        self.ring = ring
        self.Z = None if Z is None else _zfun(Z)


class GapPort:
    """Lumped port across a strip (delta gap). Reference plane = the gap."""
    kind = 'gap'

    def __init__(self, layer, center, direction, width, z0=50.0, name=''):
        self.lp = LinePort(layer, center, direction, width, 0.0)
        self.layer, self.zc, self.name = layer, z0, name

    c = property(lambda self: self.lp.c)
    d = property(lambda self: self.lp.d)
    w = property(lambda self: self.lp.w)

    def line(self, s=0.0): return self.lp.line(s)


class ViaPort:
    """Lumped port inside a via (EMF in its lowest chain, + toward the top;
    sign=-1: + toward the bottom, a port whose reference plane is above it, so
    the signal is + against its reference either way)."""
    kind = 'via'

    def __init__(self, via, z0=50.0, name='', sign=1):
        self.via, self.zc, self.name, self.layer, self.sign = (via, z0,
                                                               name, None,
                                                               float(sign))


class DiffPort:
    """
    Differential wave port on a pair of parallel feeds (positive / negative
    line), e.g. an edge-coupled pair. Contributes two modal ports to the
    S-matrix: '<name>d' (differential) and '<name>c' (common). Each mode is
    fitted with its own propagation constant (odd/even modes of a symmetric
    pair), so mode conversion and modal dispersion are captured.
      pos, neg : LinePort for each line (same direction, layer, sampling and
                 reference plane)
      zdiff, zcomm : modal reference impedances (2 Z_odd and Z_even / 2 of the
                 pair; see line_char_pair)
    """
    def __init__(self, pos, neg, zdiff=100.0, zcomm=25.0, name='P'):
        if not (np.allclose(pos.d, neg.d)):
            raise ValueError(
                "differential lines must run in the same direction")
        self.pos, self.neg, self.zdiff, self.zcomm, self.name = (pos, neg,
                                                                 zdiff, zcomm,
                                                                 name)
        # the modes' impedances over frequency (model.characterize)
        self.zd_fn = self.zc_fn = None
        # and their propagation constants
        self.gd_fn = self.gc_fn = None

    def modes(self):
        return [ModePort(self, 'd'), ModePort(self, 'c')]


class ModePort(LinePort):
    """One mode of a DiffPort (internal): geometry of the positive line plus the
    negative line."""
    def __init__(self, parent, mode):
        p = parent.pos
        super().__init__(p.layer, p.c, p.d, p.w, p.ref, s0=p.s0, step=p.step,
                         s1=p.s1,
                         zc=(parent.zdiff if mode == 'd' else parent.zcomm),
                         name=parent.name + mode)
        self.parent, self.mode, self.lp_n = parent, mode, parent.neg
        # Id = (I+ - I-)/2,  Ic = I+ + I-
        self.sign_n, self.cscale = (-1.0, 1.0) if mode == 'd' else (1.0, 2.0)
        if (fn := parent.zd_fn if mode == 'd' else parent.zc_fn) is not None:
            self.zc_fn = fn
        if (fn := parent.gd_fn if mode == 'd' else parent.gc_fn) is not None:
            # the wave fit's gamma (sparams), not fitted on the feed
            self.gamma_fn = fn


class SeriesLumped:
    """Series element across a strip (cut line of the given width at
    `center`)."""
    def __init__(self, layer, center, direction, width, Z, name=''):
        self.lp = LinePort(layer, center, direction, width, 0.0)
        self.layer, self.Z, self.name = layer, _zfun(Z), name


class Metal:
    """Conductor loss: Zs = (gamma/sigma) coth(gamma t), gamma = (1+j)/delta
    (-> 1/(sigma t) at DC). rough (m RMS): its resistance times Hammerstad and
    Jensen's factor 1 + 2/pi atan(1.4 (rough / delta)^2) at each frequency.
    finish: a surface finish's metal layers, outermost first, [(sigma, t m),
    ...], on the interfaces of `on` ({interface: share}): there Zs times 1 +
    share (F - 1), F the finished face's impedance over the bare one's (each
    finish layer a line section Z_i = gamma_i / sigma_i loading the one beneath,
    down to the copper), share the part of the strip's loss on its finished
    faces (the outer face and the sides)."""
    def __init__(self, sigma=5.8e7, t=35e-6, rough=0.0, finish=(), on=None):
        self.sigma, self.t, self.rough = sigma, t, rough
        self.finish, self.on = [tuple(x) for x in finish], dict(on or {})

    def zs(self, f, k=None):
        """Zs at f; k: the interface (its finish, if any)."""
        g = (1 + 1j) * np.sqrt(2 * np.pi * f * MU0 * self.sigma / 2)
        z = g / self.sigma / np.tanh(g * self.t)
        if self.rough:
            delta = 1 / np.sqrt(np.pi * f * MU0 * self.sigma)
            z = (z.real * (1 + 2 / np.pi * np.arctan(1.4 *
                                                     (self.rough / delta)**2))
                 + 1j * z.imag)
        share = self.on.get(k, 0.0) if self.finish else 0.0
        if share:
            z = z * (1 + share * (self.face_factor(f) - 1))
        return z

    def face_factor(self, f):
        """The finished face's impedance over the bare copper face's at f."""
        w = 2 * np.pi * f
        gam = lambda s: (1 + 1j) * np.sqrt(w * MU0 * s / 2)
        bare = gam(self.sigma) / self.sigma / np.tanh(gam(self.sigma) * self.t)
        z = bare
        for s, tl in reversed(self.finish):
            zc, th = gam(s) / s, np.tanh(gam(s) * tl)
            z = zc * (z + zc * th) / (zc + z * th)
        return z / bare

    def zs_edges(self, f, edge_layer):
        """Zs per RWG edge (by its layer's interface): one value unless a finish
        covers some interfaces."""
        if not (self.finish and self.on):
            return self.zs(f)
        out = np.full(len(edge_layer), self.zs(f), complex)
        for k in self.on:
            out[edge_layer == k] = self.zs(f, k)
        return out


def _same_area(x, y, area, sides):
    """The regular `sides`-gon of the given area about (x, y)."""
    return shapely.Polygon(
        ngon(x, y, np.sqrt(2 * area / (sides * np.sin(2 * np.pi / sides))),
             sides))


def _simplify_outside(geom, zone, max_area=1.0e-6, sides=4):
    """Small pieces of copper wholly outside `zone` (pads of stitching vias, a
    few hundred um across) as regular `sides`-gons of the same area about their
    centroid: their outline carries next to no current, and a round pad costs
    one unknown per side (the spokes of the via's fan)."""
    out = []
    for p in getattr(geom, 'geoms', [geom]):
        if p.area < max_area and not p.intersects(zone) and not p.interiors:
            q = _same_area(p.centroid.x, p.centroid.y, p.area, sides)
            p = q if q.is_valid and q.buffer(1e-9).contains(p.centroid) else p
        out.append(p)
    return shapely.unary_union(out)


# _square_vias: copper around a via within this radius is a pad (or an antipad,
# for a hole)
PAD_MAX = 0.75e-3


def _square_vias(geom, pts, sides=4):
    """The pads and antipads of the vias at `pts` as squares, for a coarse mesh
    around them: a round pad has ~30 edges and its via an octagon, each a spoke
    of the fan; a square, 4. A pad on its own becomes a square of the same area;
    one joined to a trace becomes the square around it (it keeps the joint), and
    a round hole (an antipad) around a via that does not attach here a square
    hole of the same area."""
    for x, y in pts:
        P = shapely.Point(x, y)
        parts = list(getattr(geom, 'geoms', [geom]))
        hit = [i for i, p in enumerate(parts) if p.buffer(1e-9).contains(P)]
        if hit:
            p = parts[hit[0]]
            r = min([p.exterior.distance(P)]
                    + [h.distance(P) for h in p.interiors])
            ring_ = P.buffer(1.6 * r).difference(P.buffer(r))
            if (r <= 0 or r > PAD_MAX
                    or p.intersection(ring_).area > 0.4 * ring_.area):
                # a via in a pour (near its edge or a hole) or on a trace: the
                # vertex is enough
                continue
            if p.area < 1.2 * np.pi * r * r:              # a pad by itself
                q = _same_area(x, y, p.area, sides)
            else:  # a pad on a trace: the square that holds the round pad
                q = p.union(
                    shapely.Polygon(ngon(x, y, r / np.cos(np.pi / sides),
                                         sides)))
            if q.is_valid and q.buffer(1e-9).contains(P):
                parts[hit[0]] = q
        else:
            for i, p in enumerate(parts):
                ins = list(p.interiors)
                j = next((j for j, h in enumerate(ins)
                          if shapely.Polygon(h).contains(P)), None)
                if (j is None
                        or shapely.Polygon(ins[j]).area > np.pi * PAD_MAX**2):
                    continue
                new = shapely.Polygon(
                    p.exterior, ins[:j] + ins[j + 1:] + [
                        _same_area(x, y,
                                   shapely.Polygon(ins[j]).area, sides).exterior
                    ])
                if new.is_valid:
                    parts[i] = new
                break
        geom = shapely.unary_union(parts)
    return geom


# ---- Simulation -------------------------------------------------------------
class Simulation:
    def __init__(self, stack, shapes, ports=(), vias=(), lumped=(), dmax=0.3e-3,
                 dmax_layer=None, w_ref=None, ncross=6, grade=1.0, near_fac=5.0,
                 mesher='auto', metal=None, solver='auto',
                 refine=1.0e-3, precision='auto', gpu='auto', verbose=True,
                 title='', mesh_only=False, quads=False,
                 quad_tol=35.0, zone=None, dmax_out=None,
                 stitching_vias='auto', signal=None,
                 coarsen=None, grow=0.5, structured='auto',
                 gap_rings=(1 / 4, 1 / 8,
                            1 / 16), frills=(), slots=(), dmax_slot=None,
                 sides=None, edge=None):
        # frills (frill.Frill): the holes of the stack's internal PEC planes
        # (stack.pec) that a via runs through slots (slots.Slot): openings of
        # the stack's PEC planes, each meshed, its field the unknowns (slot
        # planes) mesh_only: stop once the mesh and the unknowns (Ne, Nv) are
        # known (a cost estimate; nothing to solve with) sides (experimental):
        # dict(t, up, s_max) of mom25d.sides.Sides, thick copper's sides as
        # plates (their unknowns after the openings') edge: dict(t, up) of
        # mom25d.edge.Edges, thick copper's edges as corrected
        # near terms coarsen:
        # cells grow from dmax at the features (corners, vias, ports) to coarsen
        # x dmax away from them, by grow x the distance (geo.mesh_polygons):
        # straight runs and plane interiors in larger cells. structured:
        # straight runs of trace as a grid of rectangles (geo.mesh_polygons);
        # 'auto': with quads
        self.st, self.title, self.metal, self.solver, self.log = (
            stack, title, metal, solver, RunLog(verbose))
        # 'auto' | 'vulkan' | 'emulate' | 'off' (mixed direct solver LU)
        self.gpu = gpu
        # 'auto' | 'double' | 'mixed' (direct: complex64 Z + refinement)
        self.precision = precision
        # solver='blr' preconditioner settings: 1e-3 left antenna_cp_patch
        # (40.8k unknowns) unsolved (GMRES 500 iterations), 1e-5 solves it in 7;
        # eta 0.3 against 0.7: less memory, as fast (fast._solve_blr)
        self.blr_tol, self.blr_tile, self.blr_eta = 1e-5, 512, 0.3
        # 'auto' | 'off': the CPU's complex64 LU kept for the next frequencies
        self.reuse = 'auto'
        # 'auto' | 'on' | 'off': the direct
        self.loop_tree = os.environ.get('MOM25D_LOOP_TREE', 'auto')
        # solver in the loop/tree basis
        # (looptree.py) at low frequency
        # (auto: below policy.LOOP_TREE_BELOW
        # of (k h)^2)
        self.dmax, self.ncross, self.grade, self.near_fac = (dmax, ncross,
                                                             grade, near_fac)
        self.user_ports = list(ports)
        self.ports = [
            m for p in ports
            for m in (p.modes() if isinstance(p, DiffPort) else [p])
        ]
        self.vias, self.lumped = list(vias), list(lumped)
        self.vias += [
            p.via
            for p in self.ports
            if isinstance(p, ViaPort) and p.via not in self.vias
        ]
        self.frills = list(frills)
        # (one plane's openings together: one Y block each)
        self.slots = sorted(slots, key=lambda s: s.k)
        for sl in self.slots:
            if not stack.is_pec(sl.k) or sl.k not in stack.pec:
                raise ValueError(
                    f"an opening is in a PEC plane of the stack (M{sl.k} is "
                    "not one)"
                )
            for v in self.vias:
                if (v.k_bot <= sl.k <= v.k_top
                        and sl.poly.buffer(1e-9).contains(
                            shapely.Point(v.x, v.y))):
                    raise ValueError(
                        f"via {v.name} is in an opening of the plane M{sl.k}: "
                        "not modelled with slot planes (the whole stack "
                        "meshes the plane)")
        if self.slots and self.frills:
            raise ValueError(
                "openings of PEC planes (slots) and via holes in them (frills) "
                "together: not modelled yet"
            )
        for fr in self.frills:
            if (not (fr.k in stack.pec and fr.via in self.vias
                     and fr.via.k_bot < fr.k < fr.via.k_top)):
                raise ValueError(
                    "a hole is in a PEC plane inside the stack, around a via "
                    f"through it (M{fr.k}, {fr.via.name})"
                )
        self.layers = sorted(shapes)
        for k in self.layers:
            if not (k in stack.metal_interfaces):
                raise ValueError(
                    f"interface {k} cannot carry metal in this stack")
        geoms = {k: to_geometry(shapes[k]) for k in self.layers}
        self.zone = zone
        if zone is not None and stitching_vias == 'auto':
            # (experimental) zone: where the current crowds (the signal copper
            # and around it, BoardModel). Outside it the copper is meshed
            # plainly: no edge rings, no via octagons, small pads (stitching
            # vias) as coarse polygons, and no refinement of the layers nearby
            # under it
            geoms = {k: _simplify_outside(g, zone) for k, g in geoms.items()}
        # how each via is meshed: 'round' (its pad and antipad as drawn, an
        # octagon of the via radius) or, a stitching via, n sides ('square' =
        # 4): pad and antipad as n-gons, the barrel a single vertex (an n-gon
        # on a structured mesh); 'auto': square outside the zone. Signal vias
        # and those of hand-built models are round
        m = stitching_vias
        stitching_vias = m if m in ('round', 'auto') else (
            4 if m == 'square' else int(m))
        if stitching_vias not in ('round', 'auto') and stitching_vias < 3:
            raise ValueError(
                "a stitching via is 'auto', 'round', 'square' or n >= 3 sides")

        def via_mode(v):
            if getattr(v, 'kind', None) != 'stitching':
                return 'round'
            if stitching_vias == 'auto':
                return (4 if zone is not None
                        and not zone.contains(shapely.Point(v.x, v.y)) else
                        'round')
            return stitching_vias

        self.via_mode = {id(v): via_mode(v) for v in self.vias}
        for n in sorted({m for m in self.via_mode.values() if m != 'round'}):
            vs = [v for v in self.vias if self.via_mode[id(v)] == n]
            geoms = {
                k: _square_vias(g, [(v.x, v.y)
                                    for v in vs
                                    if v.k_bot <= k <= v.k_top], sides=n)
                for k, g in geoms.items()
            }
        self.geoms = geoms
        dmax_layer = dmax_layer or {}

        # ---- via chains: split where the via touches metal ------------------
        self.chains = []      # (via, k_bot, k_top)
        self.via_attach = []  # per via: list of metal interfaces it attaches to
        holes = {(id(fr.via), fr.k) for fr in self.frills}
        for v in self.vias:
            # (a PEC plane inside the stack holds every via through it that has
            # no hole there)
            on = (lambda k: (k in geoms and geoms[k].buffer(1e-9).contains(
                shapely.Point(v.x, v.y))) or (k in stack.pec and
                                              (id(v), k) not in holes))
            for k in (v.k_bot, v.k_top):
                if not (stack.is_pec(k) or on(k)):
                    raise ValueError(
                        f"via {v.name} ends on M{k} where there is no metal")
            att = ([v.k_bot]
                   + [k for k in range(v.k_bot + 1, v.k_top) if on(k)]
                   + [v.k_top])
            self.via_attach.append([k for k in att if k in geoms])
            self.chains += [(v, a, b) for a, b in zip(att[:-1], att[1:])]

        # ---- port sample positions and constraint lines ---------------------
        self.samples, lines = [], {k: [] for k in self.layers}
        add = lambda lp, xs: [
            lines[k].append((lp, x)) for k in _ls(lp.layer) for x in xs
        ]
        for p in self.ports:
            if isinstance(p, LinePort):
                step = p.step or dmax
                s0, s1 = (p.s0 if p.s0 is not None else 2 * step,
                          p.s1 if p.s1 is not None else p.ref)
                s = (s1 - step
                     * np.arange(int(np.floor(
                         (s1 - s0) / step + 1e-9)) + 1)[::-1])
                self.samples.append(s)
                for lp in [p] + ([p.lp_n] if isinstance(p, ModePort) else []):
                    add(lp, np.append(s, 0.0))
            else:
                self.samples.append(None)
                if isinstance(p, GapPort):
                    add(p.lp, [0.0])
        for el in self.lumped:
            add(el.lp, [0.0])
        # each line's ends on the copper's edge where that is within SNAP of
        # them (a board's feed 0.2 um wider than its port: a sliver between
        # the cut's end and the edge, where a side crossed the cut)
        self._segs = {(id(lp), x, k): self._snap(geoms[k], lp, x)
                      for k in self.layers for lp, x in lines[k]}

        # ---- mesh -----------------------------------------------------------
        with self.log.timer('mesh'):
            nodes, tris, tl, off, self.mesher_used = [], [], [], 0, {}
            via_vertex = {}
            for k in self.layers:
                dm, lp_here = (dmax_layer.get(k,
                                              dmax), [lp for lp, _ in lines[k]])
                vias_here = [(i, v)
                             for i, v in enumerate(self.vias)
                             if k in self.via_attach[i]]
                use_tensor = (mesher == 'tensor'
                              or (mesher == 'auto'
                                  and is_manhattan_rects(shapes[k])
                                  and not vias_here and all(lp.axis_aligned
                                                            for lp in lp_here)))
                wr = w_ref.get(k) if isinstance(w_ref, dict) else w_ref
                if use_tensor:
                    xl, yl = [], []
                    for lp, s in lines[k]:
                        x = abs(lp.d[0]) > 0.5
                        (xl if x else yl).append(
                            (lp.c + lp.d * s)[0 if x else 1])
                    nd, tr = make_mesh_ml(shapes[k], dm, ncross,
                                          1.01 * max(wr or 0, 1.5 * dm), xl, yl,
                                          grade=grade)
                    vids = []
                else:
                    wr = wr or self._guess_wref(geoms[k], lp_here)
                    seg = [self._segs[(id(lp), s, k)] for lp, s in lines[k]]
                    struct = bool(quads) if structured == 'auto' else structured

                    def vpt(v):
                        m = self.via_mode[id(v)]
                        if m == 'round':
                            return (v.x, v.y, v.a if v.ring else 0.0)
                        # an n-sided (stitching) via: its barrel a single
                        # vertex, or a square on a structured mesh, which aligns
                        # its grid with it (geo._run_grid) and may snap it to a
                        # grid cell (its fan: that cell)
                        if struct and m == 4 and v.ring:
                            return (v.x, v.y, v.a, 4, True)
                        return (v.x, v.y, 0.0)
                    vpts, fine = [vpt(v) for _, v in vias_here], None
                    if refine and dm > 1.2 * dmax:
                        # resolve return currents under/over metal on nearby
                        # layers
                        near = [
                            (geoms[j] if zone is None
                             else geoms[j].intersection(zone)).buffer(
                                 3 * abs(stack.z[j] - stack.z[k]) + 2 * dmax)
                            for j in self.layers
                            if j != k and abs(stack.z[j] - stack.z[k]) < refine
                        ]
                        fine = shapely.unary_union(near) if near else None
                    zkw = (dict(edge_zone=zone, split_chains=True)
                           if zone is not None else {})
                    dm_mesh, dm_fine = dm, (2 * dmax if fine is not None
                                            else None)
                    if zone is not None and dmax_out and dmax_out > dm:
                        # outside the zone the copper carries little current:
                        # cells up to dmax_out there, dm (or the refinement's 2
                        # dmax) inside it
                        zf = geoms[k].intersection(zone)
                        # else: all of it is signal
                        if not zf.is_empty and zf.area < 0.99 * geoms[k].area:
                            fine = (zf if fine is None else shapely.unary_union(
                                [fine, zf]))
                            dm_mesh, dm_fine = dmax_out, min(dm, dm_fine or dm)
                    # a meshed plane is coarsened outside its fine regions only
                    # (under the copper of the layers near it, where the return
                    # current runs): with Triangle those never were in effect
                    # (its two passes left them at their size), and coarsened
                    # they cost S 0.03-0.08 at 6-10 GHz (rf_msvia, sma_launch,
                    # demo_transition against openEMS; 2026-11-15)
                    nd, tr, vids = mesh_polygons(
                        geoms[k], dm_mesh, wr, ncross, grade, lines=seg,
                        vias=vpts, fine=fine, dmax_fine=dm_fine,
                        coarsen=(coarsen.get(k) if isinstance(coarsen, dict)
                                 else coarsen), grow=grow,
                        coarsen_fine=not (dm > 1.2 * dmax), structured=struct,
                        gap_rings=gap_rings, **zkw)
                for (i, v), vid in zip(vias_here, vids):
                    if not (vid >= 0):
                        raise ValueError(f"via {v.name}: not on the M{k} mesh")
                    via_vertex[(i, k)] = vid + off
                self.mesher_used[k] = 'tensor' if use_tensor else 'polygon'
                nodes.append(nd)
                tris.append(tr + off)
                tl.append(np.full(len(tr), k))
                off += len(nd)
            self.rwg, self.tri_layer = (RWG(np.vstack(nodes), np.vstack(tris)),
                                        np.concatenate(tl))
            self.edge_layer = self.tri_layer[self.rwg.tp]
        r = self.rwg
        # Nv: the chains, then the holes' unknowns (their field shapes'
        # amplitudes: frill.py), then the openings' (their field in RWG
        # functions: slots.py)
        for sl in self.slots:
            # an opening as a meshed plane is: cells of dmax_slot (default
            # SLOT_FACTOR x dmax), refined to 2 dmax where copper on another
            # layer is near it (3 heights + 2 dmax round it); its rim's rings as
            # a trace's
            dz = {k: abs(stack.z[k] - stack.z[sl.k]) for k in self.layers}
            near = [
                geoms[k].buffer(3 * dz[k] + 2 * dmax)
                for k in self.layers
                if dz[k] < refine
            ]
            fine = (shapely.unary_union(near).intersection(sl.poly)
                    if near else None)
            ds = dmax_slot or SLOT_FACTOR * dmax
            sl.mesh(
                ds, ncross, grade,
                fine=(fine if fine is not None and not fine.is_empty
                      and ds > 2.4 * dmax else None), dmax_fine=2 * dmax,
                w_max=4 * dmax)
        self.Ne = r.Ne
        self.sides = None
        if sides:
            from .sides import Sides
            # (no current along a side across a lumped port's gap or a lumped
            # element: the sheet's edges there carry it all)
            # (a wave port's gap is across them too: excitation)
            wave = {id(q) for p in self.ports if isinstance(p, LinePort)
                    for q in [p] + ([p.lp_n] if isinstance(p, ModePort)
                                    else [])}
            self.sides = Sides(self, **sides, gaps={
                k: [self._segs[(id(lp), x, k)] for lp, x in lines[k]
                    if x == 0.0 and id(lp) not in wave]
                for k in self.layers})
            self.log.note(self.sides.notes(sides.get('s_max')))
            if not len(self.sides):
                self.sides = None
        self.Nv = (len(self.chains) + sum(fr.count for fr in self.frills)
                   + sum(sl.count for sl in self.slots)
                   + (len(self.sides) if self.sides is not None else 0))
        if mesh_only:
            return
        self.tri_idx = {
            k: np.nonzero(self.tri_layer == k)[0]
            for k in self.layers
        }
        Nt = len(r.tris)

        # ---- via fans and extended divergence -------------------------------
        self.fan = {
            key: np.nonzero((r.tris == vid).any(1))[0]
            for key, vid in via_vertex.items()
        }
        self.F = np.array(sorted({t
                                  for v in self.fan.values()
                                  for t in v}), int)
        fpos = {t: j for j, t in enumerate(self.F)}
        self.dF = np.zeros((len(self.F), self.Nv))
        for n, (v, kb, kt) in enumerate(self.chains):
            i = self.vias.index(v)
            for k, div in ((kb, +1.0), (kt, -1.0)):
                if (i, k) in self.fan:
                    ts = self.fan[(i, k)]
                    self.dF[[fpos[t] for t in ts], n] += div / r.area[ts].sum()
        self.log.note(
            f"mesh: {Nt} triangles, {r.Ne} edge + {len(self.chains)} via "
            "unknowns"
            + (f" + {len(self.frills)} holes in PEC planes"
               if self.frills else '')
            + (f" + {sum(sl.count for sl in self.slots)} in "
               f"{len(self.slots)} opening(s) of PEC planes"
               if self.slots else '')
            + "  (" + ", ".join(
                f"M{k}: {len(self.tri_idx[k])} tri ({self.mesher_used[k]})"
                for k in self.layers) + ")")

        V = r.nodes[r.tris]
        self.tri_size = np.linalg.norm(V - np.roll(V, 1, axis=1), axis=2).max(1)
        self.P3, self.W3 = quad_pts(r, _BAR3), r.area[:, None] * _W3[None, :]
        self.P6, self.W6 = quad_pts(r, _BAR6), r.area[:, None] * _W6[None, :]
        self.edge = None
        if edge:
            from .edge import Edges
            with self.log.timer('edge corrections (static, once)'):
                self.edge = Edges(self, **edge)
            self.log.note(self.edge.notes())
            if not len(self.edge):
                self.edge = None
        ext = np.vstack([r.nodes] + [sl.rwg.nodes for sl in self.slots])
        self.rho_max = 1.05 * np.hypot(*(ext.max(0) - ext.min(0))) + 1e-4
        self._slot_cache = {}
        self._gram, self.qT = None, None
        self._edge_sets()
        # port gaps, sample cuts and lumped elements keep their edges as
        # unknowns (quad cells)
        keep_e = [i for g in self.gap if g is not None for i in g[0]]
        keep_e += [
            i for c in self.cuts if c is not None for idx, _ in c for i in idx
        ]
        keep_e += [i for e in self.lumped_edges for i in e[0]]
        if quads:
            # (experimental) near-rectangular triangle pairs as quad cells:
            # their diagonal is no longer an unknown (quads.py); the matrix is
            # filled on the triangles and projected before the LU
            from .quads import quad_subspace
            excl = self.F
            if quads != 'all':
                # no triangle with a corner on the copper outline: the edge
                # strip, where the current crowds, stays triangles (quads there:
                # rf_gcpw 0.015 from a fine reference at 9 GHz, hairpin 0.033 at
                # 3.35 GHz; kept: 0.002 and 0.009, the triangles' 0.0015 and
                # 0.007). 'centre': also none off the signal copper where it is
                # known (BoardModel): no pours or pads. 'all': quads wherever
                # triangles pair up
                edges, count = np.unique(
                    np.sort(r.tris[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), 1),
                    axis=0, return_counts=True)
                bnode = np.zeros(len(r.nodes), bool)
                # the nodes on the copper outline
                bnode[edges[count == 1].ravel()] = True
                off_ = bnode[r.tris].any(1)
                if quads == 'centre' and signal is not None:
                    off_ |= (~shapely.contains_xy(signal, r.cent[:, 0],
                                                  r.cent[:, 1]))
                excl = np.union1d(self.F, np.nonzero(off_)[0])
            with self.log.timer('quad cells'):
                self.qT, qi = quad_subspace(r, r.Ne + self.Nv,
                                            exclude_tris=excl,
                                            exclude_edges=keep_e,
                                            tol_deg=quad_tol)
            self.quad_info = qi
            self.log.note(
                f"quads: {qi['quads']} quad cells ({qi['tris_merged']} of "
                f"{qi['tris']} triangles), {qi['full']} -> {qi['reduced']} "
                "unknowns")
        if solver not in ('auto', 'direct', 'blr'):
            raise ValueError(f"solver: auto, direct or blr, not {solver!r}")
        if self.qT is not None and solver not in ('direct', 'auto'):
            self.log.note(f"quads: not with solver {solver} yet - ignored")
            self.qT = None
        self.tables, self.last = {}, None
        for p in self.ports:
            if (isinstance(p, LinePort) and not isinstance(p, ModePort)
                    and getattr(p, 'zc_fn', None) is None and p.zc == 50.0):
                self.log.note(
                    f"note: wave port {p.name} uses zc = 50 ohm. Set it to the "
                    "feed line's Zc (line_char / line_char_xs, or "
                    "BoardModel.characterize) - a wrong zc makes S "
                    "non-reciprocal when ports differ")
        n = r.Ne + self.Nv
        from .fast import cap_blas_threads, FastOperator
        from .policy import memory_plan, device, GPU_MIN_N, BLR_MIN_N
        cap_blas_threads()
        # mixed above MIXED_MIN_N (policy.memory_plan): the complex64 LU on the
        # GPU (5.7 s a solve against 14.9 s on the via transition, 13.5k
        # unknowns, RX 7800 XT) or on the CPU (about 1.3x faster than double)
        from .gpu import get_backend
        n_lu = self.qT.shape[1] if self.qT is not None else n
        want_gpu = (gpu != 'off'
                    and (precision == 'mixed' or
                         (precision == 'auto' and n_lu > GPU_MIN_N)))
        avail = available_memory()
        plan = memory_plan(n, avail, device(get_backend(gpu) if want_gpu
                                            else None), precision, solver,
                           n_lu=n_lu)
        self.plan, self.precision, precision = (plan, plan['precision'],
                                                plan['precision'])
        if solver in ('auto', 'direct'):
            if plan['path'] == 'blr':
                self.log.note(
                    "memory: the block low-rank solver needs "
                    f"~{plan['host'] / 1e9:.1f} GB (from {BLR_MIN_N} unknowns "
                    "where the GPU's hybrid does not hold the matrix), "
                    f"{avail / 1e9:.1f} GB available")
            else:
                self.log.note(
                    f"memory: direct solver needs ~{plan['host'] / 1e9:.1f} GB "
                    f"({precision} precision, {plan['path']}"
                    + (f", {plan['gpu'] / 1e9:.1f} GB on the GPU" if plan['gpu']
                       else '') + f"), {avail / 1e9:.1f} GB available")
            if solver == 'auto':
                solver = plan['solver']
            elif not plan['fits']:
                solver = 'blr'
                self.log.note(
                    "WARNING: direct solver would need "
                    f"~{plan['host'] / 1e9:.1f} GB but only {avail / 1e9:.1f} "
                    f"GB is available - switching to {solver}. Coarser dmax / "
                    "dmax_layer, a smaller region, or an inner-layer ground "
                    "reduce N."
                )
            self.solver = solver
            if self.qT is not None and solver != 'direct':
                self.log.note(f"quads: not with solver {solver} yet - ignored")
                self.qT = None
            self.log.note(f"solver: {solver} (N = {n})")
        self.fast = FastOperator(self)

    @staticmethod
    def _guess_wref(g, lps):
        ws = [lp.w for lp in lps if lp.w > 0]
        if ws:
            return min(ws)
        # no line ports here: the narrowest conductor, 2 A / P per piece (= w
        # for a strip, larger for pads/planes)
        x0, y0, x1, y1 = g.bounds
        return min([0.25 * min(x1 - x0, y1 - y0)] + [
            2 * q.area / q.length
            for q in getattr(g, 'geoms', [g])
            if q.geom_type == 'Polygon' and q.length > 0
        ])

    # ---- edges on lines -----------------------------------------------------
    @staticmethod
    def _snap(geom, lp, s):
        """lp.line(s), each end moved to the copper's edge within SNAP x w."""
        a, b = lp.line(s)
        c, h = 0.5 * (a + b), 0.5 * lp.w
        ln = shapely.LineString([c - lp.t * h * (1 + SNAP),
                                 c + lp.t * h * (1 + SNAP)])
        cut = ln.intersection(geom)
        parts = [g for g in getattr(cut, 'geoms', [cut])
                 if g.geom_type == 'LineString' and not g.is_empty]
        on = [g for g in parts if g.distance(shapely.Point(c)) < 1e-12]
        if not on:
            return a, b
        u = (np.asarray(on[0].coords) - c) @ lp.t
        lo, hi = u.min(), u.max()
        lo = lo if lo > -h * (1 + SNAP) + 1e-12 else -h
        hi = hi if hi < h * (1 + SNAP) - 1e-12 else h
        if not (abs(lo + h) <= SNAP * h and abs(hi - h) <= SNAP * h):
            return a, b
        return c + lp.t * lo, c + lp.t * hi

    def _line_edges(self, lp, s, tol=1e-8):
        r = self.rwg
        e0, e1 = (r.ends[:, 0] - (lp.c + lp.d * s), r.ends[:, 1] -
                  (lp.c + lp.d * s))
        on = ((np.abs(e0 @ lp.d) < tol) & (np.abs(e1 @ lp.d) < tol)
              & np.isin(self.edge_layer, _ls(lp.layer)))
        hw = lp.w / 2 * (1 + SNAP) + tol  # (_snap's ends)
        on &= (np.abs(e0 @ lp.t) <= hw) & (np.abs(e1 @ lp.t) <= hw)
        idx = np.nonzero(on)[0]
        return idx, np.sign(r.nrm[idx] @ lp.d)

    def _edge_sets(self):
        self.gap, self.cuts = [], []
        for p, s in zip(self.ports, self.samples):
            if isinstance(p, ModePort):
                def cut(x, k=0.5):
                    (ip, sp), (i_n, sn) = (self._line_edges(p, x),
                                           self._line_edges(p.lp_n, x))
                    return (np.concatenate([ip, i_n]),
                            np.concatenate([k * sp, k * p.sign_n * sn]))
                gap = cut(0.0, 1.0)
            elif isinstance(p, LinePort):
                cut = lambda x: self._line_edges(p, x)
                gap = cut(0.0)
            else:
                gap, s = (self._line_edges(p.lp, 0.0)
                          if isinstance(p, GapPort) else None), None
            self.gap.append(gap)
            self.cuts.append(None if s is None else [cut(x) for x in s])
            if not (gap is None or len(gap[0])):
                raise ValueError(
                    f"port {p.name}: no mesh edges on the gap line")
            if not (s is None or all(len(c[0]) for c in self.cuts[-1])):
                raise ValueError(f"port {p.name}: sample cut off the conductor")
        self.lumped_edges = [self._line_edges(el.lp, 0.0) for el in self.lumped]
        for el, e in zip(self.lumped, self.lumped_edges):
            if not (len(e[0]) > 0):
                raise ValueError(
                    f"lumped element {el.name}: no mesh edges on its line")

    def _port_chain(self, p):
        """Index of the lowest chain of the via of a ViaPort."""
        return next(n for n, (v, _, _) in enumerate(self.chains) if v is p.via)

    # ---- matrix -------------------------------------------------------------
    def green_tables(self, f):
        """The layered-media tables of every pair of metal interfaces at f: {(a,
        b): GreenTableML}, a <= b."""
        self.tables = {}
        for i, a in enumerate(self.layers):
            for b in self.layers[i:]:
                xs = a != b and abs(self.st.z[a] - self.st.z[b]) < 3 * self.dmax
                im = (a == b and self.st.at(f).nearest_discontinuity(a)
                      < 1.5 * self.dmax)
                with self.log.timer('Green tables',
                                    f"M{a}-M{b} @ {f / 1e9:.2f} GHz"):
                    self.tables[(a, b)] = GreenTableML(f, self.st, a, b,
                                                       self.rho_max,
                                                       cross_sing=xs,
                                                       image_sing=im)
        return self.tables

    # via pairs of one span pair above which R comes from a table
    VIA_TABLE_PAIRS = 64
    # triangles x chains of the via -> strip kernel's block (64 MB)
    VIA_CHUNK = 4_000_000

    def _via_geometry(self):
        """What the via blocks take of the geometry alone, once (it had been
        made again at every frequency, in Python loops over the chains and their
        pairs): each chain's (x, y), the chains of each span, every pair (m <=
        n) grouped by its two spans with its distance (a chain with itself or
        another chain of its via: the barrel's radius; else at least the two
        radii's mean), and the fans' divergence dF as a sparse matrix."""
        if (getattr(self, '_via_geo', None) is not None
                and self._via_geo[0] == len(self.chains)):
            return self._via_geo[1]
        ch = self.chains
        x = np.array([v.x for v, _, _ in ch], float)
        y = np.array([v.y for v, _, _ in ch], float)
        a = np.array([v.a for v, _, _ in ch], float)
        via = {}
        vid = np.array([via.setdefault(id(v), len(via)) for v, _, _ in ch], int)
        span_of = [(kb, kt) for _, kb, kt in ch]
        spans = sorted(set(span_of))
        si = np.array([spans.index(s) for s in span_of], int)
        by_span = {s: np.flatnonzero(si == i) for i, s in enumerate(spans)}
        m, n = np.triu_indices(len(ch))
        d = np.where(
            vid[m] == vid[n], a[m],
            np.maximum(np.hypot(x[m] - x[n], y[m] - y[n]), 0.5 * (a[m] + a[n])))
        key = si[m] * len(spans) + si[n]
        order = np.argsort(key, kind='stable')
        groups = {}
        for g in np.unique(key):
            sel = order[np.searchsorted(key[order], g, 'left'):
                        np.searchsorted(key[order], g, 'right')]
            groups[(spans[g // len(spans)],
                    spans[g % len(spans)])] = (m[sel], n[sel], d[sel])
        from scipy import sparse
        geo = dict(x=x, y=y, a=a, by_span=by_span, groups=groups,
                   dF=sparse.csc_matrix(self.dF))
        self._via_geo = (len(ch), geo)
        return geo

    def _via_blocks(self, f, QF, charges=True):
        """Via rows/columns: B (Ne x Nv) strip-via coupling, C (Nv x Nv) via-via
        (incl. elements in vias); after the chains, the holes of the PEC planes
        inside the stack (_frill_blocks). charges=False: without the chains'
        fan-distributed end charges, the blocks' only 1/(jw) part (QF not used):
        what the loop/tree basis (looptree.py) has, the charges being in its
        charge matrix. The rest (the D and R kernels, the barrel's own term) is
        the current's: its static part cancels the end charges', so it goes as
        jw."""
        w, r = 2 * np.pi * f, self.rwg
        # the sides' folds' halves on the sheet (mom25d.sides.Sides.virtual):
        # their rows as the edges' (Bv), into the folds' rows of C at the end
        sd = self.sides if charges else None
        V = sd.virtual(self) if sd is not None else None
        Bv = np.zeros((sd.np_, self.Nv), complex) if sd is not None else None
        B, C = (np.zeros((self.Ne, self.Nv), complex),
                np.zeros((self.Nv, self.Nv), complex))
        if self.frills:
            self._frill_blocks(f, B, C, V, Bv)
        if self.slots:
            self._slot_blocks(f, B, C)
        vk = ViaKernels(f, self.st, [(kb, kt) for _, kb, kt in self.chains],
                        self.layers, self.rho_max)
        geo = self._via_geometry()
        # fan-distributed end charges (G_q between triangles, same machinery as
        # strips)
        if len(self.F) and charges:
            # (dF: a chain's few fan triangles a column; as a dense product it
            # was 1.4 s of 317 vias' 3.4 s)
            # (the chains' columns: the holes', openings' and sides' have
            # no fan; as (Nt, Nv) blocks the sides' columns were 0.2 s of
            # rf_bends' 2.8 s solve, all zeros)
            Nc = len(self.chains)
            dF = geo['dF'][:, :Nc]
            # (Nt, Nc) potential of fan charges
            T = np.asarray((dF.T @ QF.T).T)
            B[:, :Nc] += (r.D.T @ T) / (1j * w)
            C[:Nc, :Nc] += (dF.T @ T[self.F]) / (1j * w)
            if V is not None:
                Bv[:, :Nc] += (V[0].T @ T) / (1j * w)
        if self.sides is not None and charges:
            # the sides' functions (mom25d.sides): with the strips', each
            # other's and the via chains' fan charges
            with self.log.timer('sides', f"{len(self.sides)} functions"):
                Bw, Cw, QF = self.sides.blocks(self, f)
                c0 = self.Nv - len(self.sides)
                B[:, c0:] += Bw
                C[c0:, c0:] += Cw
                if len(self.F) and c0:
                    X = np.asarray(geo['dF'][:, :c0].T @ QF) / (1j * w)
                    C[:c0, c0:] += X
                    C[c0:, :c0] += X.T
        # smooth via -> strip kernel D(rho), Galerkin-tested with the RWG
        # divergence: in the compiled core the chains of a span a block at a
        # time (a chain and a layer at a time, NumPy's, had been 1.5 s of an 8 s
        # solve with 888 chains)
        from . import _core
        per = max(1, self.VIA_CHUNK // max(len(r.tris), 1))
        for span, ns in geo['by_span'].items():
            for s in range(0, len(ns), per):
                sel = ns[s:s + per]
                c = np.zeros((len(r.tris), len(sel)), complex)
                for k in self.layers:
                    idx, D = self.tri_idx[k], vk.D[(k, span)]
                    c[idx] = _core.radial_table_sum(
                        self.P6[idx, :, 0], self.P6[idx, :, 1], self.W6[idx],
                        geo['x'][sel], geo['y'][sel], D.v, D._dr)
                B[:, sel] += r.D.T @ c
                if V is not None:
                    Bv[:, sel] += V[0].T @ c
        if self.sides is not None and len(self.chains):
            # the chains' smooth potential (D) on the sides' charges, as on
            # the strips' (else a fold would see the difference between its
            # strip triangle's potential and its plate's)
            Xd = self.sides.via_d(self, vk, geo)
            c0, Nc = self.Nv - len(self.sides), len(self.chains)
            C[c0:, :Nc] += Xd
            C[:Nc, c0:] += Xd.T
        if sd is not None:
            c0 = self.Nv - len(sd)
            fd = c0 + sd.fold
            C[fd, :c0] += Bv[:, :c0]
            C[:c0, fd] += Bv[:, :c0].T
        # via-via: all pairs grouped by (span_m, span_n), each group's Hankel
        # transform evaluated once
        for (sm, sn), (m, n, dd) in geo['groups'].items():
            # many pairs: the smooth remainder from a table (the SMA launch's
            # 143k pairs took 24.6 s a solve of 26.8 for its via blocks); a few:
            # exactly
            vals = ((vk.R_table(sm, sn)(dd) if len(dd) > self.VIA_TABLE_PAIRS
                     else vk.R(sm, sn, dd)) + vk.homog(sm, sn, dd))
            C[m, n] += vals  # each pair once, in one group
            C[n[m != n], m[m != n]] += vals[m != n]
        seen, zs = set(), self.metal.zs(f) if self.metal is not None else 0.0
        for n, (v, kb, kt) in enumerate(self.chains):
            # an element in a via: in its lowest chain
            if v.Z is not None and id(v) not in seen:
                C[n, n] += v.Z(f)
            seen.add(id(v))
            # wall loss
            C[n, n] += zs * (self.st.z[kt] - self.st.z[kb]) / (2 * np.pi * v.a)
        return B, C

    def _frill_blocks(self, f, B, C, V=None, Bv=None):
        """The columns and rows of the holes' unknowns (frill.py: c and -Y),
        into B and C after the chains' (and into Bv the rows of V's halves,
        Sides.virtual, as the edges'). An axisymmetric shape is a potential on
        each interface; one of the first order (a dipole shape, its cos or its
        sin) a potential that goes as the angle about the hole, and a field with
        none (its TE part), tested on the edges themselves."""
        from .frill import FrillKernels
        r, Nc = self.rwg, len(self.chains)
        fk = FrillKernels(f, self.st, self.frills, self.layers, self.rho_max)
        geo, rems = self._via_geometry(), {}
        for m, (fr, mode) in enumerate(fk.unknowns):
            s, col = fk.shape(fr, mode), Nc + m
            dip = mode[0] if isinstance(mode, tuple) else None    # 'c' or 's'
            # the shape's potential over each triangle
            psi = np.zeros(len(r.tris), complex)
            # int of r . E, E_x, E_y (its TE part)
            field = np.zeros((3, len(r.tris)), complex) if dip else None
            for k in self.layers:
                if (s, k) not in fk.psi:
                    continue
                idx = self.tri_idx[k]
                dx, dy = (self.P6[idx, :, 0] - fr.via.x,
                          self.P6[idx, :, 1] - fr.via.y)
                dist = np.hypot(dx, dy)
                if not dip:
                    psi[idx] = (fk.psi[(s, k)](dist) * self.W6[idx]).sum(1)
                    continue
                cs, sn = (dx / np.maximum(dist, 1e-12),
                          dy / np.maximum(dist, 1e-12))
                c2, s2 = cs * cs - sn * sn, 2 * sn * cs
                psi[idx] = (fk.psi[(s, k)](dist) *
                            (cs if dip == 'c' else sn) * self.W6[idx]).sum(1)
                U = fk.U[(s, k)](dist)
                ex, ey = ((U[..., 0] + U[..., 1] * c2,
                           U[..., 1] * s2) if dip == 'c' else
                          (U[..., 1] * s2, U[..., 0] - U[..., 1] * c2))
                W = self.W6[idx]
                field[:, idx] = (((self.P6[idx, :, 0] * ex
                                   + self.P6[idx, :, 1] * ey) * W).sum(1),
                                 (ex * W).sum(1), (ey * W).sum(1))
            for (Dm, Am, Xm, Ym), Bm in [((r.D, r.Aal, r.Bx, r.By), B)] + (
                    [(V, Bv)] if V is not None else []):
                Bm[:, col] = -(Dm.T @ psi)
                if dip:  # -<f, E>: f = Aal r - (Bx, By) on a triangle
                    Bm[:, col] -= (Am.T @ field[0] - Xm.T @ field[1]
                                   - Ym.T @ field[2])
            c = (-(self.dF[:, :Nc].T @ psi[self.F])
                 if len(self.F) else np.zeros(Nc, complex))
            # the chains' remainder, by span
            for span, ns in geo['by_span'].items():
                dx, dy = geo['x'][ns] - fr.via.x, geo['y'][ns] - fr.via.y
                d = np.maximum(np.hypot(dx, dy), geo['a'][ns])
                key = (id(fr), s, span)  # (a dipole's cos and sin share it)
                if key not in rems:
                    rems[key] = fk.rem(s, span, d)
                rem = rems[key]
                if dip:  # (the via on the hole's own axis: none)
                    off = np.hypot(dx, dy) > 1e-9
                    rem = np.where(
                        off,
                        rem * (dx if dip == 'c' else dy)
                        / np.where(off, np.hypot(dx, dy), 1.0), 0.0)
                    if fr.k in span:
                        # a chain ending on the plane: a first-order shape
                        # leaves a potential on the plane outside the hole,
                        # -e1(0) cos(phi) / rho (a 2D dipole: the field is
                        # inside the hole), whose gradient its TE part cancels;
                        # the chain is charged it at that end, + at its top, -
                        # at its bottom (the fans' sign; frill.py). Without it a
                        # via ending on the plane did not excite the dipoles:
                        # two via ports either side of a hole had Z21 0.287 ohm,
                        # the hole's own 0.246, now 0.241
                        sgn = 1.0 if span[1] == fr.k else -1.0
                        e10 = float(np.real(fk.e1[s][0]))
                        d2 = np.maximum(dx * dx + dy * dy, geo['a'][ns] ** 2)
                        rem = (rem + sgn * (-e10) *
                               (dx if dip == 'c' else dy) / d2)
                c[ns] += rem
            C[:Nc, col] = C[col, :Nc] = c
            for g, other in enumerate(fk.unknowns[:m + 1]):
                C[Nc + g, col] = C[col, Nc + g] = -fk.Y(other, (fr, mode))

    def _slot_blocks(self, f, B, C):
        """The columns and rows of the openings' unknowns (slots.py: c and -Y),
        after the chains' and the holes'."""
        from .slots import SlotKernels, coupling, self_block
        w = 2 * np.pi * f
        with self.log.timer('slot kernels', f"{len(self.slots)} opening(s)"):
            kern = SlotKernels(f, self.st, self.slots, self.layers,
                               self.rho_max)
            col0 = len(self.chains) + sum(fr.count for fr in self.frills)
            coupling(self, self.slots, kern, B, C, col0)
            for p in sorted({sl.k for sl in self.slots}):
                on = [sl for sl in self.slots if sl.k == p]
                i0 = col0 + sum(sl.count for sl in self.slots if sl.k < p)
                n = sum(sl.count for sl in on)
                C[i0:i0 + n, i0:i0 + n] = (-self_block(
                    on, kern.table[p], w, self.near_fac, self._slot_cache))
            # the chain rows hold the chains' coupling (coupling's), the columns
            # the same: symmetric
            Nc = len(self.chains)
            C[col0:, :Nc] = C[:Nc, col0:].T

    # ---- excitation and port quantities -------------------------------------
    def excitation(self):
        E = np.zeros((self.Ne + self.Nv, len(self.ports)), complex)
        for j, p in enumerate(self.ports):
            if isinstance(p, ViaPort):
                E[self.Ne + self._port_chain(p), j] = p.sign
            else:
                idx, sig = self.gap[j]
                E[idx, j] = sig * self.rwg.l[idx]
                if self.sides is not None and isinstance(p, LinePort):
                    # a wave port's gap across the sides too (they run on
                    # through it: broken there, the current moved from the
                    # sheet onto them over the feed, rf_interdig's cuts' share
                    # -0.2 .. 0.6 at 3 GHz, and the line's Zc took the break's
                    # +0.2j ohm)
                    n, w = self._side_line(j, 0.0, 1.0)
                    np.add.at(E[:, j], n, w)
        return E

    def port_current(self, I, j):
        p = self.ports[j]
        if isinstance(p, ViaPort):
            return p.sign * I[self.Ne + self._port_chain(p)]
        idx, sig = self.gap[j]
        return ((sig * self.rwg.l[idx])[:, None] * I[idx]).sum(0)

    def cut_current(self, I, i):
        out = np.array([((sig * self.rwg.l[idx])[:, None] * I[idx]).sum(0)
                        for idx, sig in self.cuts[i]])
        if self.sides is not None:
            # and the sides' share (the vertical edges on the cuts)
            out += np.array([(w[:, None] * I[n]).sum(0)
                             for n, w in self._side_cuts(i)])
        return out

    def _side_cuts(self, i):
        """Port i's sample cuts' side functions: [(rows in I, weights)]."""
        sc = self.__dict__.setdefault('_sc', {})
        if i not in sc:
            k = 0.5 if isinstance(self.ports[i], ModePort) else 1.0
            sc[i] = [self._side_line(i, x, k) for x in self.samples[i]]
        return sc[i]

    def _side_line(self, i, x, k):
        """(rows in I, weights): the sides' current across wave port i's line
        at x along its feed (with a ModePort's negative line), k x."""
        p, sd = self.ports[i], self.sides
        c0 = self.Ne + self.Nv - len(sd)
        ns, ws = [], []
        for lp, sg in [(p, 1.0)] + ([(p.lp_n, p.sign_n)]
                                    if isinstance(p, ModePort) else []):
            for kk in _ls(lp.layer):
                n, w = sd.across(self, kk, self._segs[(id(lp), x, kk)], lp.d)
                ns.append(c0 + n)
                ws.append(k * sg * w)
        return np.concatenate(ns), np.concatenate(ws)

    def currents(self, f, E):
        """Basis currents for excitation columns E, with the configured solver.
        A result that isn't finite (the GPU solve once returned NaN on a
        22.7k-unknown board) is solved again on the CPU, and if that isn't
        finite either, FloatingPointError: never NaN S-parameters."""
        I = self._currents(f, E)
        if np.isfinite(I).all():
            return I
        if self.gpu != 'off' and self.solver == 'direct':
            self.log.note(
                f"WARNING the GPU solve at {f / 1e9:.4g} GHz was not finite - "
                "solving it again on the CPU"
            )
            gpu, self.gpu = self.gpu, 'off'
            try:
                I = self._currents(f, E)
            finally:
                self.gpu = gpu
            if np.isfinite(I).all():
                return I
        raise FloatingPointError(
            f"mom25d: the {self.solver} solve at {f / 1e9:.4g} GHz is not "
            f"finite (N = {len(I)}): a degenerate mesh, or a port with no "
            "return path")

    def _currents(self, f, E):
        return self.fast.solve(f, E)

    def sparams(self, f, keep=False):
        for p in self.ports:  # frequency-dependent reference impedance
            if getattr(p, 'zc_fn', None) is not None:
                p.zc = float(p.zc_fn(f))
        I, n = self.currents(f, self.excitation()), len(self.ports)
        Ap, Bp = np.zeros((n, n), complex), np.zeros((n, n), complex)
        gam, resid = np.full(n, np.nan + 0j), np.zeros(n)
        self.low_span = []
        for i, p in enumerate(self.ports):
            if isinstance(p, LinePort):
                s, Ik = self.samples[i], self.cut_current(I, i)
                # gamma from the line characterisation where there is one:
                # fitted on a short feed with a strong standing wave it came out
                # 2-10x low (radial stub, |S11| 1.19), and switching between the
                # fit and the characterised gamma at a span made S jump, which
                # the adaptive sweep chased (38 solves)
                gk = getattr(p, 'gamma_fn', None)
                p.known_gamma = gk is not None
                g = gam[i] = (gk(f) if p.known_gamma else fit_gamma(
                    Ik, abs(s[1] - s[0])))
                a, b = fit_waves(Ik, s - p.ref, g)
                M = np.stack(
                    [np.exp(-g * (s - p.ref)),
                     np.exp(g * (s - p.ref))], 1)
                # residual relative to the largest response of this port over
                # all excitations (a mode that is barely excited by some column,
                # e.g. differential under common drive, would otherwise give
                # noise / ~0)
                resid[i] = (
                    np.max(np.linalg.norm(Ik - M @ np.vstack([a, b]), axis=0))
                    / np.max(np.linalg.norm(Ik, axis=0)))
                # modal current definition (diff ports)
                cs = getattr(p, 'cscale', 1.0)
                Ap[i], Bp[i] = np.sqrt(p.zc) * cs * a, -np.sqrt(p.zc) * cs * b
                span = abs(gam[i].imag) * abs(s[-1] - s[0])
                if span < (KNOWN_SPAN if p.known_gamma else FIT_SPAN):
                    self.low_span.append(p.name)
                    self.log.note(
                        f"WARNING {f / 1e9:.2f} GHz port {p.name}: feed "
                        f"samples span only {span:.2f} rad - wave separation "
                        "unreliable (lengthen the straight feed or use a "
                        "via/gap port)"
                    )
            else:
                # port voltage = applied EMF
                Vp, Ip = np.eye(n)[i], self.port_current(I, i)
                Ap[i], Bp[i] = (Vp + p.zc * Ip) / (2 * np.sqrt(p.zc)), (
                    Vp - p.zc * Ip) / (2 * np.sqrt(p.zc))
        S = Bp @ np.linalg.inv(Ap)
        wave = np.array([isinstance(p, LinePort) for p in self.ports])
        scale = np.ones(n)
        if wave.any() and n > 1:
            S, scale = balance(S, ~wave)
            # the most transmission differed between two directions, over the
            # frequencies solved (run.solve_band says it)
            self.unbalance = max(getattr(self, 'unbalance', 0.0),
                                 float((scale.max() / scale.min())**2 - 1))
        for i in np.nonzero(resid > 1e-2)[0]:
            self.log.note(
                f"WARNING {f / 1e9:.2f} GHz port {self.ports[i].name}: "
                f"wave-fit residual {resid[i]:.1e} - sample cuts too close to "
                "a discontinuity (use s1) or too short a feed for this "
                "frequency")
        self.last = dict(
            f=f,
            I=I if keep else None,
            gamma=gam,
            resid=resid,
            # A, B: the waves of each excitation column
            A=Ap,
            B=Bp,
            scale=scale)
        return S

    def sweep(self, freqs, zc=None, currents_at=None):
        out = SimpleNamespace(f=np.asarray(freqs), S=[], gamma=[], resid=[],
                              recip=[], passiv=[], I=None, fI=None)
        fI = (None if currents_at is None else freqs[np.argmin(
            abs(np.asarray(freqs) - currents_at))])
        for f in freqs:
            if zc is not None:
                for p, z in zip(self.ports, zc(f)):
                    p.zc = z
            S = self.sparams(f, keep=(f == fI))
            out.S.append(S)
            out.gamma.append(self.last['gamma'])
            out.resid.append(self.last['resid'])
            out.recip.append(np.max(np.abs(S - S.T)))
            out.passiv.append(np.max(np.linalg.svd(S, compute_uv=False)))
            if f == fI:
                out.I, out.fI = self.last['I'], f
            if self.log.verbose:
                print(
                    f"    {f / 1e9:6.2f} GHz  fit residual "
                    f"{np.max(out.resid[-1]):.1e}  max sv(S) "
                    f"{out.passiv[-1]:.4f}"
                )
        for k in ('S', 'gamma', 'resid', 'recip', 'passiv'):
            setattr(out, k, np.array(getattr(out, k)))
        return out

    def current_density(self, I):
        """(Jx, Jy) per triangle of the currents I (N, or N x k: (ntri, k)
        each)."""
        r, I = self.rwg, I[:self.Ne]
        a = r.Aal @ I
        cx, cy = ((r.cent[:, 0], r.cent[:, 1]) if a.ndim == 1 else
                  (r.cent[:, :1], r.cent[:, 1:2]))
        return cx * a - r.Bx @ I, cy * a - r.By @ I


def balance(S, exact=None):
    """(S made reciprocal, the ports' scales): a wave port's forward and
    backward waves both carry the error of its characterised impedance (and of
    whatever else scales its currents), so the S computed is D S D^-1 of the
    true, symmetric one, D diagonal: transmission differs between directions by
    d_i^2 / d_j^2 while reflections and the products S_ij S_ji are exact. The d
    that fit |S_ij / S_ji| best are taken out, each pair weighted by |S_ij S_ji|
    and every d held towards 1 with the weight BALANCE_FLOOR: ports that reach
    each other only through weak terms (a filter's stop band, a pair's mode
    conversion: noise) are left as they are; ports `exact` (lumped ones) are
    held at 1. What is left of the asymmetry is averaged. rf_diffvia's pair on a
    microstrip and a stripline: |Sdd21| 0.962 one way and 0.985 the other at
    every frequency, 0.974 after."""
    A = np.abs(S)
    W = A * A.T
    np.fill_diagonal(W, 0.0)
    with np.errstate(divide='ignore', invalid='ignore'):
        # ln d_i - ln d_j for each pair
        R = np.where(W > 0, 0.5 * np.log(A / A.T), 0.0)
    # the weighted graph's Laplacian, and the hold
    L = np.diag(W.sum(1) + BALANCE_FLOOR) - W
    if exact is not None and np.any(exact):
        L = L + np.diag(np.where(exact, 1e6, 0.0))
    d = np.exp(np.linalg.solve(L, (W * R).sum(1)))
    S = S * (d[None, :] / d[:, None])
    return 0.5 * (S + S.T), d


def _line_fit(sim, IR, IL, step):
    """gamma and Zc of a centre-fed line from the currents (n x 1) on its two
    halves' sample cuts (ports 0, 1)."""
    g = fit_gamma(np.hstack([IR, IL]), step)
    (A, B), (C, D) = (fit_waves(IR, sim.samples[0],
                                g), fit_waves(IL, sim.samples[1], g))
    return g, 1.0 / ((A[0] - B[0]) - (C[0] - D[0]))


def line_char_xs(stack, layer, w, freqs, planes=None, coplanar=None,
                 via_pitch=1e-3, via_r=0.15e-3, length=16e-3, dmax=0.4e-3,
                 ncross=4, verbose=False, sides=None):
    """
    Zc and gamma of a strip of width w on `layer` with extra reference metal:
    planes = {interface:
    width} adds a centred wide strip on that interface (e.g. a meshed GND plane
    above or below); coplanar = [(y0, y1), ...] adds same-layer ground strips
    (grounded CPW), stitched to ground by a via row every via_pitch near their
    inner edge. Centre-fed long line, travelling-wave fit. sides: the
    Simulation's (thick copper's sides).
    """
    L2, margin = length / 2, 2e-3
    shapes = {
        layer: [(-L2, -w / 2, L2, w / 2)],
        **{k: [(-L2, -W / 2, L2, W / 2)]
           for k, W in (planes or {}).items()}
    }
    vias = []
    for y0, y1 in (coplanar or []):
        shapes[layer].append((-L2, y0, L2, y1))
        inner = y0 if abs(y0) < abs(y1) else y1
        yv = (inner + np.sign(y1 + y0)
              * min(0.5 * abs(y1 - y0), max(2 * via_r, 0.4e-3)))
        vias += [Via(x, yv, 0, layer, radius=via_r, name='fence')
                 for x in np.arange(-L2 + via_pitch / 2, L2, via_pitch)]
    # well-conditioned fit at low frequency
    step = max(dmax, (L2 - margin - 1e-3) / 8)
    ports = [
        LinePort(layer, (0, 0), (d, 0), w, L2 - margin, s0=1e-3, step=step,
                 name=n) for d, n in ((1, 'R'), (-1, 'L'))
    ]
    wr = {
        layer: w,
        **{k: min(W, 2e-3)
           for k, W in (planes or {}).items() if k != layer}
    }
    sim = Simulation(stack, shapes, ports, vias=vias, dmax=dmax, w_ref=wr,
                     ncross=ncross, verbose=verbose, refine=0, solver='auto',
                     sides=sides)
    out = []
    for f in freqs:
        I = sim.currents(f, sim.excitation()[:, [0]])
        out.append(
            _line_fit(sim, sim.cut_current(I, 0), sim.cut_current(I, 1), step))
    return tuple(np.array(out).T)                                   # gamma, Zc


def line_char_pair(stack, layer, w, gap, freqs, planes=None, length=16e-3,
                   dmax=0.4e-3, ncross=4, verbose=False, sides=None):
    """
    Even/odd modes of a symmetric edge-coupled pair (strip width w, edge gap
    `gap`) on `layer`, with optional reference metal (planes = {interface:
    width}). Returns (gamma_e, Z_even, gamma_o, Z_odd); the DiffPort reference
    impedances are Z_diff = 2 Z_odd and Z_comm = Z_even / 2. sides: as
    line_char_xs.
    """
    L2, margin = length / 2, 2e-3
    yc = (w + gap) / 2
    shapes = {
        layer: [(-L2, yc - w / 2, L2, yc + w / 2),
                (-L2, -yc - w / 2, L2, -yc + w / 2)],
        **{k: [(-L2, -W / 2, L2, W / 2)]
           for k, W in (planes or {}).items()}
    }
    step = max(dmax, (L2 - margin - 1e-3) / 8)
    ps = [
        LinePort(layer, (0, s * yc), (d, 0), w, L2 - margin, s0=1e-3, step=step,
                 name=f"{s}{d}") for s in (1, -1) for d in (1, -1)
    ]  # (+,R) (+,L) (-,R) (-,L)
    wr = {layer: w, **{k: min(W, 2e-3) for k, W in (planes or {}).items()}}
    sim = Simulation(stack, shapes, ps, dmax=dmax, w_ref=wr, ncross=ncross,
                     verbose=verbose, refine=0, solver='auto', sides=sides)
    out = []
    for f in freqs:
        E = sim.excitation()
        # even / odd drive at x = 0
        I = sim.currents(f, np.stack([E[:, 0] + E[:, 2], E[:, 0] - E[:, 2]], 1))
        IR, IL = sim.cut_current(I, 0), sim.cut_current(I, 1)  # line + only
        out.append([
            *_line_fit(sim, IR[:, [0]], IL[:, [0]], step),
            *_line_fit(sim, IR[:, [1]], IL[:, [1]], step)
        ])
    return tuple(np.array(out).T)  # gamma_e, Z_even, gamma_o, Z_odd


def coupled_stripline_cohn(w, s, b, er):
    """Exact Z_even, Z_odd of zero-thickness edge-coupled strips centred between
    grounds spaced b."""
    from scipy.special import ellipk
    a = np.pi * w / (2 * b); c = np.pi * (w + s) / (2 * b)
    ke = np.tanh(a) * np.tanh(c); ko = np.tanh(a) / np.tanh(c)
    Z = lambda k: 30 * np.pi / np.sqrt(er) * ellipk(1 - k ** 2) / ellipk(k ** 2)
    return Z(ke), Z(ko)
