"""Laminates by name (R-E4): the library's lookup, a stackup's values kept, its missing ones filled and said."""
import pytest

from kicad2ems import materials


def test_names_are_matched_whatever_their_spelling():
    assert materials.lookup('Rogers RO4350B')['name'] == 'RO4350B'
    assert materials.lookup('ro4350b lopro')['name'] == 'RO4350B'
    assert materials.lookup('FR-4')['name'] == 'FR4' and materials.lookup('FR4')['fref'] is None   # generic: none
    assert materials.lookup('Isola 370HR')['fref'] == 1e9 and materials.lookup('RO4003C')['er'] == 3.55
    assert materials.lookup('unobtainium') is None and materials.lookup(None) is None


def test_a_stackups_missing_values_come_from_its_material_or_fr4_with_a_note():
    from kicad2ems.board import Board
    b = Board.__new__(Board)
    b.notes = []
    given = b._material(dict(kind='diel', name='core', er=3.48, tand=0.004, material='RO4350B'))
    assert (given['er'], given['tand'], given['fref'], given['material']) == (3.48, 0.004, 10e9, 'RO4350B') and not b.notes
    filled = b._material(dict(kind='diel', name='core', er=None, tand=None, material='Rogers RO4003C'))
    assert (filled['er'], filled['tand']) == (3.55, 0.0027) and 'material library' in b.notes[-1]
    bare = b._material(dict(kind='diel', name='prepreg', er=4.2, tand=None, material=None))
    assert (bare['er'], bare['tand'], bare['fref']) == (4.2, 0.02, None) and "FR-4's taken" in b.notes[-1]


def test_a_known_laminate_holds_its_values_at_its_own_frequency():
    pytest.importorskip('mom25d')
    from mom25d.multilayer import djordjevic_sarkar
    e = djordjevic_sarkar(3.66, 0.0037, 10e9, 10e9)                  # (the wideband model, as the solvers use it)
    assert e.real == pytest.approx(3.66) and -e.imag / e.real == pytest.approx(0.0037)
