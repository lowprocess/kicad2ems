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
    p.write_text(json.dumps(formats.stamp({'solvers': ['mom25d']}, 'kicad2ems-run')))
    assert frontend.read_summary(str(p))['solvers'] == ['mom25d']
    p.write_text(json.dumps(dict(formats.stamp({}, 'kicad2ems-run'), format_version=99)))
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
    first = json.loads(p.read_text().splitlines()[0])
    assert first['kind'] == 'format' and first['format'] == 'kicad2ems-events'
    assert [e['kind'] for e in frontend.EventTail(str(p)).read()] == ['stage']
    lines = p.read_text().splitlines()
    lines[0] = json.dumps(dict(first, format_version=99))
    p.write_text('\n'.join(lines) + '\n')
    ev = frontend.EventTail(str(p)).read()
    assert ev[0]['kind'] == 'log' and 'newer' in ev[0]['text'] and ev[1]['kind'] == 'stage'
