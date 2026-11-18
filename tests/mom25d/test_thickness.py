"""mom25d.thickness.resolve: the mode each request becomes on each kind of board
and ports, and the reason the run logs."""
from types import SimpleNamespace as NS

import pytest

from kicad2ems.mom25d import thickness
from kicad2ems.mom25d.geo import LinePort
from kicad2ems.mom25d.sim import Via, ViaPort

WAVE = [LinePort(1, (0, 0), (1, 0), 0.3e-3, 2e-3, name='P1')]
LUMPED = [ViaPort(Via(0, 0, 0, 1), name='P2')]
SIDES_PLANES = "no sides with planes '{}' (not modelled)"
SIDES_WHOLE = "no sides with the whole stack meshed (not modelled)"
WAVE_NOTE = ("no {} with wave ports (P1; not modelled), the copper as sheets: "
             "--mom-port lumped for them")


def expected(req, planes, whole, wave):
    """(mode, the reason's parts) as docs/mom.md#thickness states the rules."""
    mode, why = req, []
    if req == 'sides' and (whole or planes != 'pec'):
        mode = 'off' if planes in ('holes', 'slots') else 'edge'
        why.append(SIDES_WHOLE if whole else SIDES_PLANES.format(planes))
    if mode in ('sides', 'edge') and wave:
        why.append(WAVE_NOTE.format(mode))
        mode = 'off'
    return mode, why


@pytest.mark.parametrize('wave', [False, True])
@pytest.mark.parametrize('whole', [False, True])
@pytest.mark.parametrize('planes', ['pec', 'mesh', 'holes', 'slots'])
@pytest.mark.parametrize('req', thickness.MODES)
def test_every_request_on_every_board(req, planes, whole, wave):
    model = NS(planes=planes, whole=whole)
    mode, why = thickness.resolve(req, model, WAVE if wave else LUMPED)
    want, parts = expected(req, planes, whole, wave)
    assert mode == want
    if not parts:
        assert why is None
    else:
        assert why == (f"thickness {req!r}: {'; '.join(parts)}, thickness "
                       f"{want!r}")


TABLE = [  # (requested, planes, whole, wave) -> mode
    ('sides', 'pec', False, False, 'sides'),
    ('sides', 'pec', False, True, 'off'),
    ('sides', 'mesh', False, False, 'edge'),
    ('sides', 'mesh', False, True, 'off'),
    ('sides', 'pec', True, False, 'edge'),
    ('sides', 'holes', False, False, 'off'),
    ('sides', 'slots', True, False, 'off'),
    ('edge', 'holes', False, False, 'edge'),
    ('edge', 'pec', False, True, 'off'),
    ('grow', 'mesh', True, True, 'grow'),
    ('off', 'pec', False, True, 'off'),
]


@pytest.mark.parametrize('req, planes, whole, wave, mode', TABLE)
def test_the_table(req, planes, whole, wave, mode):
    """The rules' cases spelt out (the generated test's oracle checked)."""
    got, _ = thickness.resolve(req, NS(planes=planes, whole=whole),
                               WAVE if wave else LUMPED)
    assert got == mode == expected(req, planes, whole, wave)[0]


def test_the_board_alone_and_wave_sides(monkeypatch):
    """ports None: the board's rules alone (BoardModel's); WAVE_SIDES keeps the
    sides with wave ports; grow+sides and others are refused."""
    pec = NS(planes='pec', whole=False)
    assert thickness.resolve('sides', pec) == ('sides', None)
    mesh = NS(planes='mesh', whole=False)
    assert thickness.resolve('sides', mesh)[0] == 'edge'
    monkeypatch.setattr(thickness, 'WAVE_SIDES', True)
    assert thickness.resolve('sides', pec, WAVE) == ('sides', None)
    for bad in ('grow+sides', 'thick'):
        with pytest.raises(ValueError, match='off, grow, sides or edge'):
            thickness.resolve(bad, pec)


def test_the_simulation_takes_the_resolved_mode():
    """BoardModel and PlanarModel.simulation through resolve: lumped ports
    keep the sides, wave ports make sheets of them and say so once."""
    from kicad2ems.mom25d.board import BoardModel
    from test_board import board, quiet
    data = board('thru')
    for port, has in (('via', True), ('wave', False)):
        m = BoardModel(data, port=port, log=quiet)
        sim = m.simulation(dmax=0.5e-3, fmin=2e9, fmax=4e9, verbose=False,
                           characterize=False, mesh_only=True)
        assert (sim.sides is not None) == has, port
        said = [n for n in m.notes if n.startswith("thickness 'sides'")]
        assert len(said) == (0 if has else 1), port
