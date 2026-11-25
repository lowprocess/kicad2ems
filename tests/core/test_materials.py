"""Laminates by name (R-E4): the library's lookup, a stackup's values kept, its
missing ones filled and said."""
import pytest

from kicad2ems.core import materials


def test_names_are_matched_whatever_their_spelling():
    assert materials.lookup('Rogers RO4350B')['name'] == 'RO4350B'
    assert materials.lookup('ro4350b lopro')['name'] == 'RO4350B'
    # generic: none
    assert (materials.lookup('FR-4')['name'] == 'FR4'
            and materials.lookup('FR4')['fref'] is None)
    assert (materials.lookup('Isola 370HR')['fref'] == 1e9
            and materials.lookup('RO4003C')['er'] == 3.55)
    assert (materials.lookup('unobtainium') is None
            and materials.lookup(None) is None)


def test_a_stackups_missing_values_come_from_its_material_or_fr4_with_a_note():
    from kicad2ems.core.board import Board
    b = Board.__new__(Board)
    b.notes = []
    given = b._material(
        dict(kind='diel', name='core', er=3.48, tand=0.004, material='RO4350B'))
    assert ((given['er'], given['tand'], given['fref'], given['material'])
            == (3.48, 0.004, 10e9, 'RO4350B') and not b.notes)
    filled = b._material(
        dict(kind='diel', name='core', er=None, tand=None,
             material='Rogers RO4003C'))
    assert ((filled['er'], filled['tand']) == (3.55, 0.0027)
            and 'material library' in b.notes[-1])
    bare = b._material(
        dict(kind='diel', name='prepreg', er=4.2, tand=None, material=None))
    assert ((bare['er'], bare['tand'], bare['fref']) == (4.2, 0.02, None)
            and "FR-4's taken" in b.notes[-1])


def test_a_known_laminate_holds_its_values_at_its_own_frequency():
    pytest.importorskip('kicad2ems.mom25d')
    from kicad2ems.mom25d.multilayer import djordjevic_sarkar
    # (the wideband model, as the solvers use it)
    e = djordjevic_sarkar(3.66, 0.0037, 10e9, 10e9)
    assert (e.real == pytest.approx(3.66)
            and -e.imag / e.real == pytest.approx(0.0037))


def test_the_raw_stacks_readers_take_a_dielectric_without_er():
    """KiCad's "Not specified" is er None in KiCadBoard's stack_items: the
    readers of the raw stack (si's and coupling's time grid, segment's cross
    sections, the impedance calculator) take Board's values (the file's, its
    material's, FR-4's; a mask 3.3), where max() and sums of None had raised
    TypeError."""
    from types import SimpleNamespace
    from kicad2ems.core.segment import _xs_of
    er_of = materials.er_of
    assert er_of(dict(kind='diel', er=3.0)) == 3.0
    assert er_of(dict(kind='diel', er=None, material='RO4003C')) == 3.55
    assert er_of(dict(kind='diel', er=None, material=None)) == 4.5
    assert er_of(dict(kind='diel', tand=None), 'tand') == 0.02
    assert er_of(dict(kind='mask', er=None)) == 3.3
    kb = SimpleNamespace(stack_items=[
        dict(kind='copper', name='F.Cu', t=0.035e-3),
        dict(kind='diel', name='p', t=0.2e-3, er=None, material=None),
        dict(kind='copper', name='In1.Cu', t=0.035e-3),
        dict(kind='diel', name='c', t=1.0e-3, er=None, material='RO4003C'),
        dict(kind='copper', name='B.Cu', t=0.035e-3)])
    kind, h, er = _xs_of(kb, 'F.Cu')
    assert kind == 'microstrip' and h == pytest.approx(0.2) and er == 4.5
