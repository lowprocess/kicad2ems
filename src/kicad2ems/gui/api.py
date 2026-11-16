"""
kicad2ems.gui's API: what the other parts may import (the KiCad plugin's
window, kicad2ems.ipc). The rest of kicad2ems.gui is the window's own.

    PlanView     the board seen from above, the window's plan view and the KiCad
                 plugin's map
    SectionView  the field across a line-model section: show_section(a drawing
                 element, or None)

A subclass of PlanView may use, beside its public methods (load, set_board,
set_lines_visible, current_layer, z_of, redraw, fit, select) and its `selected`
signal: `plot` (the pyqtgraph PlotWidget it draws in), `mesh` (the mesh
selector), and override redraw() and the mouse hooks _moved(scene_pos) and
_clicked(event), calling the base's for what it does not handle itself.
"""


def __getattr__(name):  # PySide6 and pyqtgraph load on first use
    if name == 'PlanView':
        from .planview import PlanView
        return PlanView
    if name == 'SectionView':
        from .sectionview import SectionView
        return SectionView
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['PlanView', 'SectionView']
