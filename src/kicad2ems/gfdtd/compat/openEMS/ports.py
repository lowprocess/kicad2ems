"""openEMS.ports over gfdtd."""
from kicad2ems.gfdtd.ports import *  # noqa: F401,F403
from kicad2ems.gfdtd.ports import UI_data  # noqa: F401

import numpy as _np


def _load_ui_file(filepath):
    """An openEMS probe file (gfdtd writes them as openEMS does): (data (N,
    ncols), the last header line's column names or None); openEMS's own, which
    its utilities (DelayFidelity) import from here."""
    comments, rows = [], []
    with open(filepath) as f:
        for line in f:
            if line.startswith('%'):
                comments.append(line[1:].strip())
            elif line.strip():
                rows.append(line.split())
    col_names = comments[-1].split() if comments else None
    if col_names is not None and col_names[0] != 't/s':
        raise ValueError(f'{filepath}: unexpected first column '
                         f'"{col_names[0]}", expected "t/s"')
    return _np.array(rows, dtype=_np.double), col_names
