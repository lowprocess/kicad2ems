"""The study with no window, the rest of it: the config as data
(document.StudyDocument: read, changed, written back, what this machine cannot
show kept) and a Run's queue (runqueue.RunQueue: the order, a run passed over
when what it needs did not finish, Stop). Neither imports Qt: a program that
drives a study without the window (the KiCad plugin, a test) uses them as the
window does."""
import subprocess
import sys
from types import SimpleNamespace

from kicad2ems.core import analyses, study
from kicad2ems.core.study import Study
from kicad2ems.gui.document import StudyDocument
from kicad2ems.gui.runqueue import RunQueue


def test_neither_module_needs_qt():
    code = ("import sys; import kicad2ems.gui.document, kicad2ems.gui.runqueue;"
            " bad = [n for n in sys.modules if n.startswith('PySide6')];"
            " assert not bad, bad")
    p = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stderr


CONFIG = '''# a comment that stays
board = "b.kicad_pcb"
fmax = 6.0

[study]
run = ["dc", "cfd_question"]

[[analysis.pdn]]
rail = "+5V"
board = "other.kicad_pcb"

[[analysis.pdn]]
rail = "+3V3"

[analysis.cfd]
fans = 2
'''


def test_a_config_read_changed_and_written_back(tmp_path):
    cfg = tmp_path / 'c.toml'
    cfg.write_text(CONFIG, encoding='utf-8')
    (tmp_path / 'b.kicad_pcb').write_text('')
    # this machine: pdn installed, cfd not; the questions it has engines for
    doc = StudyDocument.read(str(cfg), installed={'pdn', 'thermal'},
                             questions={'dc': ['pdn'], 'pdn': ['pdn']})
    assert doc.path == str(cfg) and doc.values['fmax'] == 6.0
    assert doc.values['board'] == str(tmp_path / 'b.kicad_pcb')
    # the first table shown, its board made whole; the rest a run each
    assert doc.table('pdn') == dict(rail='+5V',
                                    board=str(tmp_path / 'other.kicad_pcb'))
    assert [t['rail'] for t in doc.runs('pdn')] == ['+5V', '+3V3']
    # not installed here, and a question no engine here answers: kept apart
    assert doc.others == dict(cfd=dict(fans=2))
    assert (list(doc.study.asked) == ['dc']
            and list(doc.foreign.asked) == ['cfd_question'])
    assert not doc.dirty and doc.notes == []
    # a change, as a program makes it without the window
    doc.values = dict(doc.values, fmax=8.0)
    doc.tables['pdn']['rail'] = '+5V0'
    doc.study = Study({'dc': ['pdn'], 'thermal': []})
    doc.dirty = True
    out = tmp_path / 'saved.toml'
    cfg.rename(out)  # (the template is the file read: moved, then written)
    doc.path = str(out)
    doc.write(str(out))
    text = out.read_text(encoding='utf-8')
    assert '# a comment that stays' in text and 'fmax = 8.0' in text
    assert not doc.dirty and doc.path == str(out)
    sets = analyses.read_sets(str(out))
    # the first changed, its path relative again; the second kept
    assert sets['pdn'] == [dict(rail='+5V0', board='other.kicad_pcb'),
                           dict(rail='+3V3')]
    assert sets['cfd'] == [dict(fans=2)]
    # what this machine could not show: written back with the rest
    assert (study.read_config(str(out), strict=False).asked
            == {'dc': ['pdn'], 'thermal': [], 'cfd_question': []})


def test_a_plain_config_stays_plain(tmp_path):
    cfg = tmp_path / 'rf.toml'
    cfg.write_text('board = "b.kicad_pcb"\n', encoding='utf-8')
    doc = StudyDocument.read(str(cfg), installed=set())
    assert doc.study.asked == {'sparams': []} or not doc.study.asked
    assert any(n.startswith('board not found') for n in doc.notes)
    doc.write(str(cfg))
    text = cfg.read_text(encoding='utf-8')
    assert '[analysis' not in text and '[study' not in text


def test_write_study_leaves_the_settings(tmp_path):
    """The window saves the RF settings from its form; the study and the
    analyses' tables alone go through write_study, the rest of the file as it
    is."""
    cfg = tmp_path / 'c.toml'
    cfg.write_text('board = "b.kicad_pcb"\nfmin = 1.5\n', encoding='utf-8')
    doc = StudyDocument(installed={'pdn'})
    doc.write_study(str(cfg), tables=dict(pdn=dict(rail='+1V8')),
                    study=Study({'dc': ['pdn']}))
    text = cfg.read_text(encoding='utf-8')
    assert 'board = "b.kicad_pcb"' in text and 'fmin = 1.5' in text
    assert analyses.read_config(str(cfg))['pdn'] == dict(rail='+1V8')


def _step(kind, engine):
    return SimpleNamespace(kind=kind, engines=[engine])


def test_a_runs_queue(monkeypatch):
    from kicad2ems.gui import runqueue
    # thermal needs the circuit's losses (as an Analysis's `needs` says)
    needs = dict(thermal=['circuit'])
    monkeypatch.setattr(runqueue, 'unmet', lambda name, failed: [
        n for n in needs.get(name, []) if n in failed])
    said = []
    q = RunQueue(on_state=lambda k, t, bad, s: said.append((k, t, bad)))
    steps = [_step('solvers', 'rf'), _step('analysis', 'circuit'),
             _step('analysis', 'thermal')]
    keys = {id(s): k for s, k in zip(steps, ('rf', 'circuit', 'thermal'))}
    assert q.start(steps, key_of=lambda st: keys[id(st)]) == [
        'rf', 'circuit', 'thermal']
    # one at a time
    assert q.next() == 'rf' and q.job == 'rf' and q.next() is None
    q.began('rf')
    assert q.ended('circuit', 0) is None      # not the one going
    assert q.ended('rf', 0) >= 0 and q.job is None
    assert said[-1][:2][0] == 'rf' and said[-1][1].startswith('done')
    # the circuit could not start: the thermal run that takes its losses is
    # passed over, said so (core.study.unmet: thermal needs the circuit)
    assert q.next() == 'circuit'
    q.not_started('circuit', 'no model')
    assert said[-1] == ('circuit', 'not started: no model', True)
    assert q.next() is None and q.queue == [] and 'thermal' in q.failed
    assert said[-1][0] == 'thermal' and said[-1][1].startswith('not run:')


def test_stop():
    said = []
    q = RunQueue(on_state=lambda k, t, bad, s: said.append((k, t, bad)))
    steps = [_step('solvers', 'rf'), _step('analysis', 'pdn')]
    names = iter(('rf', 'pdn'))
    q.start(steps, key_of=lambda st: next(names))
    q.began(q.next())
    assert q.stop() and q.queue == [] and ('pdn', 'not run: stopped',
                                           False) in said
    # the run going ends stopped, not failed
    q.ended('rf', 1)
    assert said[-1] == ('rf', 'stopped', False) and q.job is None
    assert not q.stop()
