"""Shared 2D layout overlay, in model mm or reflected to the field viewer's
KiCad axes."""
import numpy as np
from PySide6.QtGui import QColor, QPen, QBrush
from .planview import _path, _item, _text
from .volume3d import rings


def layout_items(geometry, layer, kicad=False, components=True):
    flip = [1, -1 if kicad else 1]

    def path(polys):
        return _path([[np.asarray(r, float) * flip for r in p] for p in polys])
    active = geometry.get('active_copper', {})
    groups = [(geometry.get('inactive_copper',
                            geometry.get('copper',
                                         {})), '#a8adb3', '#737b82', -8)]
    if active:
        groups.append((active, '#46c6d2', '#007c88', -7))
    items = [
        _item(path(copper[layer]), QPen(QColor(line), .9), QBrush(QColor(fill)),
              z) for copper, fill, line, z in groups if copper.get(layer)
    ]
    for c in geometry.get('components', []) if components else []:
        if c['layer'] != layer:
            continue
        polys = rings(c['polygon'])
        col = QColor('#ffd166' if c.get('active') else '#a7a9ad')
        col.setAlpha(180)
        it = _item(path(polys), QPen(QColor('#282828'), 1.2), QBrush(col), 8)
        it.setToolTip(
            f"{c['ref']} {c.get('value', '')} — "
            f"{c.get('shape_source', 'package approximation')}"
        )
        items.append(it)
        if c.get('active'):
            corners = np.asarray(polys[0][0], float)
            xy = (corners.min(0) + corners.max(0)) / 2
            items.append(
                _text(c['ref'], (xy[0], -xy[1] if kicad else xy[1]), 9,
                      color='#151515', anchor=(.5, .5)))
    return items
