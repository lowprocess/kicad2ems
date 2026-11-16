"""The files kicad2ems writes for other programs carry their format and its version (formats.py), and the readers
of api.frontend check them: the run summary, the events, sysinfo's JSON and the antenna results."""
import json
import pytest
from kicad2ems import formats
from kicad2ems.api import frontend
from kicad2ems.events import EventSink


def test_stamp_and_check():
    d = formats.stamp({'a': 1}, 'kicad2ems-run')
    assert d == {'a': 1, 'format': 'kicad2ems-run', 'format_version': formats.FORMATS['kicad2ems-run']}
    assert formats.check(d, 'kicad2ems-run') is None and formats.check({'a': 1}, 'kicad2ems-run') is None  # older
    assert 'not a kicad2ems-antenna' in formats.check(d, 'kicad2ems-antenna')
    newer = dict(d, format_version=d['format_version'] + 1)
    assert 'update kicad2ems' in formats.check(newer, 'kicad2ems-run')


def test_readers_check(tmp_path):
    p = tmp_path / 'b_run.json'
    p.write_text(json.dumps(formats.stamp({'solvers': ['mom25d']}, 'kicad2ems-run')), encoding='utf-8')
    assert frontend.read_summary(str(p))['solvers'] == ['mom25d']
    p.write_text(json.dumps(dict(formats.stamp({}, 'kicad2ems-run'), format_version=99)), encoding='utf-8')
    with pytest.raises(ValueError, match='newer'):
        frontend.read_summary(str(p))
    from kicad2ems.fields.antenna import save
    a = tmp_path / 'b_antenna.json'
    save(dict(solver='mom25d', sphere=[1, 2]), str(a))
    assert frontend.read_antenna(str(a)) == formats.stamp(dict(solver='mom25d'), 'kicad2ems-antenna')
    from kicad2ems.sysinfo import info
    assert frontend.check_format(json.loads(json.dumps(info())), 'kicad2ems-sysinfo') is None


def test_events_name_their_format(tmp_path):
    """The stream's first line is its format: EventTail does not pass it on, and says when it cannot read it."""
    p = tmp_path / 'ev.jsonl'
    sink = EventSink(str(p))
    sink.emit('stage', name='model')
    sink.close()
    first = json.loads(p.read_text(encoding='utf-8').splitlines()[0])
    assert first['kind'] == 'format' and first['format'] == 'kicad2ems-events'
    assert [e['kind'] for e in frontend.EventTail(str(p)).read()] == ['stage']
    lines = p.read_text(encoding='utf-8').splitlines()
    lines[0] = json.dumps(dict(first, format_version=99))
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    ev = frontend.EventTail(str(p)).read()
    assert ev[0]['kind'] == 'log' and 'newer' in ev[0]['text'] and ev[1]['kind'] == 'stage'


def test_touchstone_from_other_programs(tmp_path):
    """Another program's files: Y and Z (normalised to R in v1, in ohms in v2) are converted to S at their
    reference, a v1 noise block ends the data, v2's [Number of Ports] and a lower triangle are read (any file had
    been taken as S, R ignored, the port count taken from the extension, and a noise block failed)."""
    import numpy as np
    from kicad2ems.touchstone import read_touchstone, write_touchstone
    (tmp_path / 'z.s2p').write_text("# GHz Z RI R 75\n1 1.3333333 0 1.3333333 0 1.3333333 0 1.3333333 0\n")
    f, S, zr = read_touchstone(str(tmp_path / 'z.s2p'), with_zref=True)          # a shunt 100 ohm, at 75 ohm
    assert f[0] == 1e9 and list(zr) == [75, 75] and S[0, 0, 0] == pytest.approx(-0.75 / 2.75, rel=1e-6)
    (tmp_path / 'y.ts').write_text("[Version] 2.0\n# GHz Y RI R 50\n[Number of Ports] 2\n[Two-Port Data Order] 12_21\n"
                                   "[Number of Frequencies] 1\n[Network Data]\n1 0.01 0 -0.01 0 -0.01 0 0.01 0\n[End]\n")
    assert np.allclose(read_touchstone(str(tmp_path / 'y.ts'))[1][0], 0.5)       # a series 100 ohm
    (tmp_path / 'n.s2p').write_text("# GHz S MA R 50\n1 .5 0 .1 0 .1 0 .5 0\n2 .5 0 .2 0 .1 0 .5 0\n"
                                    "1 1.2 .3 20 .2\n2 1.3 .3 25 .2\n")           # its noise parameters after
    f, S = read_touchstone(str(tmp_path / 'n.s2p'))
    assert len(f) == 2 and S[1, 1, 0] == pytest.approx(0.2)                       # v1: S11 S21 S12 S22
    (tmp_path / 'l.s3p').write_text("[Version] 2.0\n# GHz S RI R 50\n[Number of Ports] 3\n[Number of Frequencies] 1\n"
                                    "[Matrix Format] Lower\n[Network Data]\n1 0.1 0 0.2 0 0.3 0 0.4 0 0.5 0 0.6 0\n[End]\n")
    assert np.allclose(read_touchstone(str(tmp_path / 'l.s3p'))[1][0].real, [[.1, .2, .4], [.2, .3, .5], [.4, .5, .6]])
    S = np.array([[[0.1 + 0.2j, 0.5], [0.4, -0.3j]]] * 2)
    write_touchstone(str(tmp_path / 'own.s2p'), [1e9, 2e9], S, [50, 75])         # kicad2ems's own, v2
    f, back, zr = read_touchstone(str(tmp_path / 'own.s2p'), with_zref=True)
    assert np.allclose(back, S) and list(zr) == [50, 75]
