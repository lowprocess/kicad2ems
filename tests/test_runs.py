"""A kicad2ems run started by another program (kicad2ems.runs: kicad2ems-gui, the KiCad plugin): the settings
file it writes, the command line, the event stream read as it grows, stopping it, and the files of the newest run."""
import json, os
from kicad2ems.runs import Run, EventTail, stop_runs, find_results, write_toml, toml_value
from kicad2ems.config import build_settings


def test_run_events_and_stop(tmp_path):
    r = Run(dict(board='b.kicad_pcb', fmin=1.0), tmp_path / 'out', preview=True)
    assert r.argv()[-1] == '--preview' and r.argv()[3] == r.toml and os.path.exists(r.toml)
    tail = EventTail(r.events)
    assert tail.read() == []
    with open(r.events, 'w') as fh:
        fh.write(json.dumps(dict(kind='stage', name='model')) + "\n" + '{"kind": "log", "te')   # half a line
    assert [e['kind'] for e in tail.read()] == ['stage']
    with open(r.events, 'a') as fh:
        fh.write('xt": "hello"}\n')
    assert tail.read() == [dict(kind='log', text='hello')]
    d = tmp_path / 'out' / 'openems' / 'exc_P1'
    d.mkdir(parents=True)
    assert stop_runs(str(tmp_path / 'out')) == [str(d)] and (d / 'ABORT').exists()


def test_written_settings_read_back(tmp_path):
    vals = dict(board=str(tmp_path / 'b.kicad_pcb'), nets=['RF', 'GND'], fmin=1.0, fmax=6.0, points=201,
                ports=['P3=J1.1', 'P4@1,2,F.Cu'], cut_ports=True, region=[0.0, 0.0, 10.0, 5.0], fast='qucsator',
                fine=None, solvers=[])
    p = tmp_path / 'c.toml'
    write_toml(p, vals)
    s = build_settings({}, str(p))
    assert s.nets == ['RF', 'GND'] and s.points == 201 and s.ports == ['P3=J1.1', 'P4@1,2,F.Cu'] and s.cut_ports
    assert s.region == (0, 0, 10, 5) and s.fast == 'qucsator' and s.fine is None
    assert 'solvers = []' in p.read_text()                          # no solver: said, not left to the default
    assert toml_value(True) == 'true' and toml_value([1, 'a"b']) == '[1, "a\\"b"]'


def test_find_results(tmp_path):
    assert find_results(str(tmp_path)) == []
    (tmp_path / 'b_run.json').write_text(json.dumps(dict(files=[['S-parameters', 'b_openems.s2p'],
                                                               ['report', str(tmp_path / 'b_report.html')]])))
    assert find_results(str(tmp_path)) == [('S-parameters', str(tmp_path / 'b_openems.s2p')),
                                           ('report', str(tmp_path / 'b_report.html'))]
