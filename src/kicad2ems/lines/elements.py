"""
The line model's data: Element (one section between two nodes) and LineModel (the netlist, the ports'
paths, coupled groups and discontinuities) that builder.build fills and fastsolve.py, the report, the GUI's
overlay and the KiCad plugin read. Units: mm, Ohm; model coordinates (y up).
"""
import math
import numpy as np
from ..constants import C0


# ---- the line model ---------------------------------------------------------------------------------
class Element:
    """One piece of the line model between nodes a and b ('gnd' for a shunt part).
    kind: 'trace' | 'pad' | 'via' | 'part'. xs: the cross-section ('microstrip', 'embedded microstrip',
    'stripline', 'offset stripline', 'cpwg', 'coax', 'lumped', 'no reference'). Lengths mm."""
    def __init__(self, kind, a, b, length, **kw):
        self.kind, self.a, self.b, self.length = kind, a, b, float(length)
        self.layer = kw.get('layer', '')
        self.w = kw.get('w')
        self.pts = kw.get('pts')                    # model coordinates, for drawing
        self.geom = kw.get('geom')                  # pad polygon (model coordinates)
        self.region = kw.get('region')              # a strip's own copper (strips.py): its width along it
        self.radial = kw.get('radial')              # a radial stub's sector (strips.radial_sector), or None
        self.label = kw.get('label', '')
        self.net = kw.get('net', '')
        self.part = kw.get('part')                  # parts.Part for 'part'
        self.xs, self.z0, self.eeff, self.er, self.h, self.s, self.t = 'no reference', None, None, None, None, None, 0.0
        self.z0_sheet = self.eeff_sheet = None      # zero-thickness copper (openEMS sheets)
        self.tand, self.L = 0.0, None               # dielectric loss, via inductance (H) when no antipad
        self.offset = 0.0                           # centre of a widened trace's copper off its centre line (mm)
        self.group = None                           # index into LineModel.groups for a coupled line
        self.notes = []

    @property
    def delay_ps(self):
        if self.kind == 'part' or self.eeff is None:
            return None
        return self.length * 1e-3 * math.sqrt(self.eeff) / C0 * 1e12

    def row(self):
        return dict(kind=self.kind, label=self.label, layer=self.layer, xs=self.xs, w=self.w, length=self.length,
                    z0=self.z0, eeff=self.eeff, z0_sheet=self.z0_sheet, eeff_sheet=self.eeff_sheet, er=self.er, h=self.h, s=self.s, delay_ps=self.delay_ps,
                    notes=list(self.notes), value=self.part.value_str() if self.part else None)


class LineModel:
    """nodes: set of node names; elements: [Element]; ports {port name: node}; port_z0 {name: ohm};
    paths [(port, other port or None, [(Element, start mm, reversed)], branches)]; notes."""
    def __init__(self):
        self.elements, self.ports, self.port_z0, self.paths, self.notes = [], {}, {}, [], []
        self.groups = []            # coupled sections: dict(members=[(Element, near node, far node)], gaps, length, ...)
        self.junctions = []         # discontinuities at nodes: dict(node, kind, arms=[(Element, end)], widths, ...)

    def adjacency(self):
        adj = {}
        for i, e in enumerate(self.elements):
            adj.setdefault(e.a, []).append((i, e.b))
            if e.b != 'gnd':
                adj.setdefault(e.b, []).append((i, e.a))
        return adj

    def summary(self):
        """JSON-friendly: every path as a list of section rows with their start positions."""
        out = []
        for p, q, secs, branches in self.paths:
            rows = []
            for e, s0, _ in secs:
                rows.append(dict(e.row(), start=s0))
            out.append(dict(port=p, to=q, z_port=self.port_z0.get(p), sections=rows,
                            branches=[dict(at=a, length=L, label=lab, z0=z) for a, L, lab, z in branches]))
        groups = [dict(lines=len(g['members']), length=g['length'], layer=g['layer'],
                       widths=[e.w for e, _, _ in g['members']], gaps=list(g['gaps']), h=g['h'], er=g['er'],
                       labels=[e.label for e, _, _ in g['members']],
                       pairs=[dict(p) for p in g['pairs']]) for g in self.groups]
        junctions = [dict(kind=j['kind'], node=j['node'], xs=j['xs'], layer=j['layer'], widths=list(j['widths']),
                          at=[float(j['xy'][0]), float(-j['xy'][1])], angle=j.get('angle'), gap=j.get('gap'))
                     for j in self.junctions]
        return dict(paths=out, notes=list(self.notes), groups=groups, junctions=junctions)

    def drawing(self, cu_z=None):
        """JSON-friendly geometry of the model for the GUI's viewer (<name>_lines.json): every element with its
        points (model mm, y up), its layer's height (cu_z: {layer: z mm}) and its row (Z0, eeff, length, delay),
        and each path as the ids of its elements in order."""
        cu_z = cu_z or {}
        rnd = lambda v: None if v is None else round(float(v), 6)
        elements, index = [], {}
        for e in self.elements:
            if e.pts is None:
                continue
            pts = np.asarray(e.pts, float).reshape(-1, 2)
            index[id(e)] = len(elements)
            r = e.row()
            elements.append(dict(id=len(elements), kind=e.kind, label=e.label, layer=e.layer, net=e.net,
                                 z=rnd(cu_z.get(e.layer)), w=rnd(e.w), xs=e.xs, z0=rnd(e.z0), eeff=rnd(e.eeff),
                                 length=rnd(e.length), delay_ps=rnd(r['delay_ps']), group=e.group,
                                 zdiff=rnd(getattr(e, 'zdiff', None)), outline=_outline(e),
                                 value=r['value'], notes=list(e.notes),
                                 pts=[[round(float(x), 5), round(float(y), 5)] for x, y in pts]))
        paths = [dict(port=p, to=q, ids=[index[id(e)] for e, _, _ in secs if id(e) in index])
                 for p, q, secs, _ in self.paths]
        z0s = [e['z0'] for e in elements if e['z0']]
        return dict(format='kicad2ems-lines', version=1, elements=elements, paths=paths,
                    port_z0=dict(self.port_z0), z0_range=[min(z0s), max(z0s)] if z0s else None)


def _outline(e):
    """A pad section's copper as drawn: the pad's own outline rings (model mm), or None (other sections are
    drawn from their centre line and width)."""
    g = getattr(e, 'geom', None)
    if e.kind != 'pad' or g is None or g.is_empty:
        return None
    polys = [g] if g.geom_type == 'Polygon' else [p for p in getattr(g, 'geoms', []) if p.geom_type == 'Polygon']
    return [[[round(float(x), 5), round(float(y), 5)] for x, y in p.exterior.coords] for p in polys] or None
