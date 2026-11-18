"""PlanView, which the KiCad plugin subclasses: the members a subclass may
use."""


def test_planview_has_what_a_subclass_uses():
    from kicad2ems.gui import application
    from kicad2ems.gui.planview import PlanView
    application()
    v = PlanView()
    for name in ('load', 'set_board', 'set_lines_visible', 'current_layer',
                 'z_of', 'redraw', 'fit', 'select', 'selected', 'plot', 'mesh',
                 '_moved', '_clicked'):
        assert hasattr(v, name), name
    import shiboken6
    # now, not at interpreter exit (a parentless plot can crash it then)
    shiboken6.delete(v)
