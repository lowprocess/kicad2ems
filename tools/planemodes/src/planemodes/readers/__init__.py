"""Board readers. load_board picks one from the path:

    .kicad_pcb                          KiCad (6-10)
    .hyp                                HyperLynx
    a directory with matrix/matrix,     ODB++
    or a .tgz / .tar.gz / .tar / .zip
"""
import os

from .hyp import read_hyp
from .kicad import read_kicad
from .odbpp import read_odbpp


def load_board(path, **kw):
    """The Board in `path` (KiCad, HyperLynx or ODB++). kw go to the reader:
    KiCad fill_unfilled=True, ODB++ step=None."""
    p = str(path)
    if p.lower().endswith('.kicad_pcb'):
        return read_kicad(p, **kw)
    if p.lower().endswith('.hyp'):
        return read_hyp(p, **kw)
    if os.path.isdir(p) or p.lower().endswith(('.tgz', '.tar.gz', '.tar', '.zip')):
        return read_odbpp(p, **kw)
    raise ValueError(f'{p}: not a .kicad_pcb or .hyp file or an ODB++ job/archive')
