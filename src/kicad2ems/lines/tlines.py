"""
Transmission-line sections: a ballpark line model of the ports' nets, in the spirit of Simbeor's
decomposition. Every net a port sits on is broken into uniform pieces - trace runs, component pads,
via barrels and lumped parts - and each piece gets its cross-section (microstrip, stripline, offset
stripline, grounded coplanar waveguide, embedded microstrip) and quasi-static Z0, effective
permittivity and delay from closed-form formulas:

  microstrip        Hammerstad & Jensen (1980), with their strip-thickness correction
  embedded          microstrip covered by dielectric: the cover correction of Bahl / Wadell
  stripline         Wheeler (1978), thickness included; offset stripline: the parallel combination
                    of the two symmetric striplines (IPC-2141 style)
  grounded CPW      conformal mapping (Ghione & Naldi), zero thickness
  via barrel        coaxial line from the drill to the antipad in the planes it passes

Pads count at their own width: a trace that ends in a pad is shortened by the part inside it, and the
pad is a section of its own (the discontinuity that KiCad's track lengths hide). Parts between two pads
continue the path (series) or end in a shunt to ground.

build(board) returns a LineModel: nodes and elements (the netlist that fastsolve.py turns into
S-parameters) and, per port, the path to the other ports as an ordered list of sections for the
report. It reads the board's primitives (board.Board.prims: tracks, pads, zones, vias). Units: mm, Ohm;
model coordinates (y up).

The model lives in four modules, re-exported here (kicad2ems.lines.tlines is what other packages import):

  formulas      the closed-form cross-section formulas above (no board)
  reference     the reference copper of a trace and the cross-section it gives (classify)
  builder       the board to sections and nodes (build)
  topology      the ports' paths, coupled lines and discontinuities
  elements      the model's data: Element and LineModel

Module settings (reference.TILE, reference.ADAPTIVE, topology.COUPLE_H, ...) are read where they are
defined: set them on that module, not on this one.
"""
from .formulas import (ETA0, MU0, ellipk, _hj_z01, _hj_eeff, microstrip, embedded_microstrip, stripline,  # noqa: F401
                       offset_stripline, cpwg, coax, via_inductance)
from .elements import Element, LineModel                                                         # noqa: F401
from .reference import RefCopper, NetCopper, classify, GAP_STEP, TILE, ADAPTIVE, WIDTH_STEP      # noqa: F401
from .topology import COUPLE_H, COUPLED_XS                                                        # noqa: F401
from .builder import build                                                                        # noqa: F401
