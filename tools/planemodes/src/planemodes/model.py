"""The board as every other module sees it, whichever file it came from.

Units are millimetres; x points right and y down (KiCad's convention - the
ODB++ reader flips y to match). Copper is held per layer and per net as
shapely geometry, already unioned.
"""
from dataclasses import dataclass, field

import shapely


@dataclass
class Layer:
    """One layer of the stack-up: 'copper' or 'dielectric'."""
    name: str
    kind: str
    thickness: float          # mm
    er: float = 4.4           # dielectrics only
    tand: float = 0.02


@dataclass
class Via:
    """A plated hole joining copper layers `top`..`bottom` (inclusive)."""
    x: float
    y: float
    drill: float              # mm, hole diameter
    pad: float                # mm, ring diameter
    top: str
    bottom: str
    net: str = ''


@dataclass
class Board:
    source: str
    stackup: list             # [Layer], top to bottom
    copper: dict              # {layer name: {net name: shapely geometry}}
    vias: list                # [Via]
    outline: object = None    # shapely (Multi)Polygon, or None
    notes: list = field(default_factory=list)   # reader warnings for the user

    @property
    def copper_layers(self):
        """Copper layer names, top to bottom."""
        return [l.name for l in self.stackup if l.kind == 'copper']

    def nets(self):
        """Every net that has copper or vias, sorted."""
        out = {n for per in self.copper.values() for n in per}
        out |= {v.net for v in self.vias}
        return sorted(n for n in out if n)

    def layer_copper(self, layer, nets=None):
        """The union of a layer's copper, of all nets or only `nets`."""
        per = self.copper.get(layer, {})
        parts = [g for n, g in per.items() if nets is None or n in nets]
        return shapely.union_all(parts) if parts else shapely.Polygon()

    def bounds(self):
        """(x0, y0, x1, y1) of the outline, else of all copper."""
        if self.outline is not None and not self.outline.is_empty:
            return self.outline.bounds
        allc = [g for per in self.copper.values() for g in per.values()]
        return shapely.union_all(allc).bounds if allc else (0, 0, 1, 1)

    def gap(self, top, bottom):
        """The dielectric between two copper layers: (thickness mm, er, tand).
        Several dielectrics act in series: er = d / sum(d_i / er_i), and the
        loss tangent is weighted by each layer's share of the field."""
        names = [l.name for l in self.stackup]
        i, j = sorted((names.index(top), names.index(bottom)))
        diel = [l for l in self.stackup[i + 1:j] if l.kind == 'dielectric']
        if not diel:
            return 0.1, 4.4, 0.02
        d = sum(l.thickness for l in diel)
        w = [l.thickness / l.er for l in diel]
        return d, d / sum(w), sum(wi * l.tand for wi, l in zip(w, diel)) / sum(w)

    def via_spans(self, via, top, bottom):
        """True if `via` passes through the whole dielectric between `top` and
        `bottom` (it reaches both layers)."""
        cl = self.copper_layers
        a, b = sorted((cl.index(via.top), cl.index(via.bottom)))
        return a <= cl.index(top) and b >= cl.index(bottom)
