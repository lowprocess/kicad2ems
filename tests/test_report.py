"""The report's interactive S-parameter plot: each solver's Touchstone file embedded as data (thinned to
PLOT_POINTS, NaN as null), the static image kept under it, and the plain image when there is no file."""
import json
import re

import numpy as np

from kicad2ems import report
from kicad2ems.touchstone import write_touchstone


def _summary(tmp_path, n=1001):
    f = np.linspace(1e9, 3e9, n)
    S = np.zeros((n, 2, 2), complex)
    S[:, 0, 0], S[:, 1, 0] = 0.1, 0.9j
    S[:, 0, 1] = np.nan                                              # P2 not driven
    write_touchstone(str(tmp_path / 'b_mom25d.s2p'), f, S, [50.0, 50.0])
    return dict(solved=dict(mom25d=dict(label='mom25d (MoM)', touchstone='b_mom25d.s2p', names=['IN', 'OUT'])))


def test_sparam_data(tmp_path):
    d = report._sparam_data(_summary(tmp_path), str(tmp_path))
    (s,) = d['solvers']
    assert s['label'] == 'mom25d (MoM)' and s['names'] == ['IN', 'OUT'] and s['first'] == ['S11', 'S21']
    assert s['params'] == ['S11', 'S21', 'S12', 'S22'] and len(s['f']) <= report.PLOT_POINTS
    assert s['f'][0] == 1.0 and s['re']['S11'][0] == 0.1 and s['im']['S21'][0] == 0.9 and s['re']['S12'][0] is None
    assert report._sparam_data(dict(solved=dict(x=dict(touchstone='gone.s2p'))), str(tmp_path)) is None


def test_report_embeds_the_plot(tmp_path):
    sm = _summary(tmp_path)
    data = report._sparam_data(sm, str(tmp_path))
    html = report._iplot(dict(data, solvers=[dict(data['solvers'][0], label='a</script>b')]))
    assert html.count('<script') == 2 and 'a<\\/script>b' in html             # no early end of the data's script
    blob = re.search(r"<script type='application/json' class='data'>(.*?)</script>", html, re.S).group(1)
    assert json.loads(blob)['solvers'][0]['label'] == 'a</script>b'
