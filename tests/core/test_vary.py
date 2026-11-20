"""Settings varied (R-E5, kicad2ems.core.vary): --vary and a config's [vary]
table, a job per combination, the list."""
import json
import os

import pytest

from kicad2ems.core import vary as V
from kicad2ems.core.config import OPTIONS, ConfigError, build_settings

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILTER = os.path.join(ROOT, 'examples', 'rf_interdig.toml')


def test_the_specs_and_their_combinations():
    spec = V.parse(['points=21,41', 'fmax=4,5'], OPTIONS)
    assert spec == [('points', ['21', '41']), ('fmax', ['4', '5'])]
    combos = V.combinations(spec)
    assert len(combos) == 4 and combos[1] == [('points', '21'), ('fmax', '5')]
    assert (V.tag(combos[1]) == 'points=21_fmax=5'
            and V.tag([('region', '0,0;1,1')]) == 'region=0-0-1-1')
    for bad, why in (('points', 'KEY=V1'), ('nope=1,2', 'not a setting'),
                     ('out=a,b', 'not a setting a run varies'),
                     ('points=21', 'two values'), ('points=a,b', '')):
        with pytest.raises(ValueError, match=why):
            V.parse([bad], OPTIONS)
    with pytest.raises(ValueError, match='given twice'):
        V.parse(['points=1,2', 'points=3,4'], OPTIONS)
    with pytest.raises(ValueError, match='at most'):
        V.parse([
            f"points={','.join(str(i) for i in range(11, 20))}", 'fmax=5,6,7,8'
        ], OPTIONS)


def test_a_vary_table_in_a_config(tmp_path):
    cfg = tmp_path / 'c.toml'
    import re
    text = re.sub(r'^board\s*=\s*"', f'board = "{ROOT}/examples/',
                  open(FILTER, encoding='utf-8').read(), flags=re.M)
    cfg.write_text(text + '\n[vary]\npoints = [21, 41]\n', encoding='utf-8')
    s = build_settings({}, str(cfg))
    assert s.vary == ['points=21,41']
    cfg.write_text(
        cfg.read_text(encoding='utf-8').replace('points = [21, 41]',
                                                'points = [21]'),
        encoding='utf-8')
    with pytest.raises(ConfigError, match='two values'):
        build_settings({}, str(cfg))


def test_each_combination_is_a_run_of_its_own(run_cli, tmp_path):
    pytest.importorskip('skrf')
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--solvers', 'scikit', '--vary',
                            'points=21,41', '--vary', 'fmax=4,5', '--no-report',
                            '--out', out)
    assert code == 0, so + err
    assert ('vary: 4 combinations of points, fmax' in so
            and 'vary: 4 of 4 solved' in so)
    d = json.loads((out / 'rf_interdig_vary.json').read_text(encoding='utf-8'))
    assert (d['keys'] == ['points', 'fmax']
            and [r['code'] for r in d['runs']] == [0] * 4)
    assert (d['runs'][2]['values'] == dict(points='41', fmax='4')
            and d['runs'][2]['code'] == 0)
    assert d['runs'][2]['folder'] == os.path.join('vary', 'points=41_fmax=4')
    assert d['runs'][2]['solved']['scikit'] == 'rf_interdig_scikit.s2p'
    got = V.load(str(out))
    from kicad2ems.core.touchstone import read_touchstone
    f, S = read_touchstone(got['runs'][3]['solved']['scikit'])
    assert (len(f) == 41 and f[-1] == pytest.approx(5e9)
            and os.path.isabs(got['runs'][0]['folder']))
    assert V.load(str(tmp_path)) is None
    # the figures side by side: in the list, the CSV and the log, a line a
    # combination and solver
    fig = d['runs'][3]['figures']['scikit']
    assert {'S11 max dB', 'S11 dip GHz', 'S21 mid dB', 'S21 min dB'} <= set(fig)
    assert fig['S21 min dB'] <= fig['S21 mid dB']
    csv = (out / 'rf_interdig_vary.csv').read_text(encoding='utf-8').splitlines()
    assert csv[0].startswith('points,fmax,solver,S11 max dB')
    assert len(csv) == 1 + sum(len(r['figures']) for r in d['runs'])
    assert any(line.startswith('41,5,scikit,') for line in csv)
    assert 'vary: the figures, a line a combination and solver' in so


def test_a_files_figures(tmp_path):
    import numpy as np
    from kicad2ems.core.touchstone import write_touchstone
    f = np.linspace(1e9, 3e9, 5)
    S = np.zeros((5, 2, 2), complex)
    S[:, 0, 0] = [0.5, 0.1, 0.01, 0.1, 0.5]
    S[:, 1, 0] = S[:, 0, 1] = [0.9, 0.95, 0.99, 0.95, 0.5]
    S[:, 1, 1] = 0.2
    p = tmp_path / 'x.s2p'
    write_touchstone(str(p), f, S, 50.0)
    fig = V.figures(str(p))
    assert fig['S11 dip GHz'] == 2.0 and fig['S11 dip dB'] == pytest.approx(-40)
    assert fig['S21 mid dB'] == pytest.approx(20 * np.log10(0.99), abs=1e-3)
    assert fig['S21 min dB'] == pytest.approx(20 * np.log10(0.5), abs=1e-3)
    assert V.figures(str(tmp_path / 'none.s2p')) == {}
