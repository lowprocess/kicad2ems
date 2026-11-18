"""Every config of the repository means what it did before the config reader
changed (data/config_compat.json, config_compat.py), read directly and saved as
the GUI saves it; and the one writer, runs.update_toml."""
import json, os, subprocess, sys

import kicad2ems
from kicad2ems.core.runs import update_toml

HERE = os.path.dirname(os.path.abspath(__file__))


def _meanings(*args):
    # this kicad2ems, the real solvers and analyses
    src = os.path.dirname(os.path.dirname(kicad2ems.__file__))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONPATH=os.pathsep
               .join([src, os.environ.get('PYTHONPATH', '')]))
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, 'config_compat.py'), *args],
        env=env, check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def test_every_config_means_what_it_did():
    with open(os.path.join(HERE, 'data', 'config_compat.json')) as fh:
        before = json.load(fh)
    now = _meanings()
    assert list(now) == list(before)
    for path, was in before.items():
        assert {k: now[path][k] for k in was} == was, path


def test_a_config_saved_as_the_gui_saves_it_means_the_same():
    """Saved: settings, solvers, study and analysis tables the same. The GUI's
    own forms: fast and mom as the solver tick boxes (the solvers that run,
    the line model's resolved); an older [analysis] run saved as [study],
    whose S-parameter run is then of S-parameters (not as the settings' mode
    says)."""
    direct, saved = _meanings(), _meanings('--roundtrip')
    for path, a in direct.items():
        b = saved[path]
        own = ('fast', 'solvers', 'mom')
        old = isinstance(a['study'], dict) and a['study']['said'] == 'analysis'
        if isinstance(a['settings'], dict):
            assert ({k: v for k, v in b['settings'].items() if k not in own}
                    == {k: v for k, v in a['settings'].items()
                        if k not in own}), path
        for k in ('solvers', 'sets', 'study', 'loose', 'plan'):
            x, y = a[k], b[k]
            if k in ('study', 'loose') and isinstance(x, dict):
                x, y = dict(x, said=0), dict(y, said=0)
            if k == 'plan' and old:
                x = [s[:3] + s[4:] for s in x]
                y = [s[:3] + s[4:] for s in y]
            assert x == y, (path, k)


def test_the_one_writer(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text(
        '# head\nfmin = 1.0   # GHz\nnets = ["A",\n  "B"]  # two\nold = 1\n\n'
        '[solver.mom25d]\ndmax = 0.4\n\n[study]\nrun = ["dc"]\n[study.dc]\n'
        'engine = "pdn"\nload = [\n  ["a", 1],\n]\n\n[other]\nx = 1  # kept\n')
    update_toml(str(p), {('', 'fmin'): 2.0, ('', 'nets'): ['C'],
                         ('', 'old'): None, ('', 'fmax'): 6.0,
                         ('solver.mom25d', 'cell'): 0.2,
                         ('solver.gfdtd', 'nz'): 4},
                {'study': {'run': ['sparams', 'pdn'], 'pdn': {
                    'engine': 'pdn'}}, 'analysis': {'pdn': [
                        dict(rail='+5V'), dict(rail='+3V3')], 'odd': {}}})
    assert p.read_text() == (
        '# head\nfmin = 2.0   # GHz\nnets = ["C"]  # two\nfmax = 6.0\n\n'
        '[solver.mom25d]\ndmax = 0.4\ncell = 0.2\n\n[study]\n'
        'run = ["sparams", "pdn"]\n\n[study.pdn]\nengine = "pdn"\n\n'
        '[other]\nx = 1  # kept\n\n[[analysis.pdn]]\n'
        'rail = "+5V"\n\n[[analysis.pdn]]\nrail = "+3V3"\n\n'
        '[solver.gfdtd]\nnz = 4\n')
