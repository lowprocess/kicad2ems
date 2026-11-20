"""The registries as kicad2ems has them (kicad2ems.core.solvers.SOLVERS,
kicad2ems.core.analyses.ANALYSES), read in a process of their own (this one's
solvers are the stand-ins, standins.py): every solver and analysis listed
loads, their names, aliases and settings are each one's own, and they load with
an optional package missing (the check() of what needs it says so)."""
import json
import subprocess
import sys

import pytest
from kicad2ems.core.analyses import KINDS, RF

# the third-party packages of pyproject's extras, and openEMS's: a solver or an
# analysis imports one only where it needs it
OPTIONAL = ('skrf', 'gmsh', 'cupy', 'slangpy', 'PySide6', 'pyqtgraph',
            'OpenGL', 'kipy', 'CSXCAD', 'openEMS')
# what --solvers takes beside the solvers' names and aliases
RESERVED = {'lines', 'line', 'fast', 'auto', 'all'}
# the solvers and analyses whose check() fails without a package
NEEDS = dict(skrf=('scikit', ),
             gmsh=('palace', 'palace_cap', 'palace_loop', 'palace_pdn'))

DESCRIBE = '''
import json, sys
for name in sys.argv[1:]:
    sys.modules[name] = None  # importing it raises ImportError
from kicad2ems.core import analyses, solvers
from kicad2ems.core.config import CORE
from kicad2ems.core.study import QUESTIONS
print(json.dumps(dict(
    listed=[len(solvers.SOLVERS), len(analyses.ANALYSES)],
    solvers=[dict(name=sv.name, aliases=list(sv.aliases), order=sv.order,
                  options=[[o.key, id(o)] for o in sv.options],
                  check=list(sv.check()))
             for sv in solvers.registry().values()],
    analyses=[dict(name=a.name, order=a.order,
                   options=[[o.key, o.kind]
                            for o in a.options()],
                   provides=list(a.provides),
                   questions=[q.name for q in a.questions],
                   check=list(a.check()))
              for a in analyses.registry().values()],
    core=[[o.key, id(o)] for o in CORE], questions=list(QUESTIONS))))
'''


def describe(*hidden):
    """What the registries hold, with the packages `hidden` missing."""
    p = subprocess.run([sys.executable, '-c', DESCRIBE, *hidden],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.splitlines()[-1])


@pytest.fixture(scope='module')
def listed():
    return describe()


def test_every_solver_and_analysis_listed_loads(listed):
    assert [len(listed['solvers']), len(listed['analyses'])] == listed['listed']
    assert [s['name'] for s in listed['solvers']] == [
        'openems', 'mom25d', 'gfdtd', 'palace', 'pcbqs', 'qucs',
        'scikit'
    ]
    assert [a['name'] for a in listed['analyses']] == [
        'crosstalk', 'coupling', 'pdn', 'elmer_dc', 'mqs', 'palace_loop',
        'palace_pdn',
        'circuit', 'elmer_mag', 'thermal', 'elmer_thermal', 'palace_cap'
    ]
    for rows in (listed['solvers'], listed['analyses']):  # by order, then name
        assert rows == sorted(rows, key=lambda r: (r['order'], r['name']))


def test_names_aliases_and_settings_are_each_one_s_own(listed):
    names = [n for s in listed['solvers'] for n in (s['name'], *s['aliases'])]
    assert len(names) == len(set(names)) and not set(names) & RESERVED
    # a setting is one Option object: a core one, or a solver's (or the FDTD
    # solvers', shared); never two of one key
    owner = dict(listed['core'])
    for s in listed['solvers']:
        for key, made in s['options']:
            assert owner.setdefault(key, made) == made, (s['name'], key)
    # an analysis's name is its own: not the S-parameter run's, not a solver's
    named = [a['name'] for a in listed['analyses']]
    assert (all(named) and len(named) == len(set(named))
            and RF not in named and not set(named) & set(names))
    for a in listed['analyses']:
        assert all(k and kind in KINDS for k, kind in a['options']), a['name']
        # the questions it answers: kicad2ems's, or its own
        assert (set(a['provides'])
                <= set(listed['questions']) | set(a['questions'])), a['name']


@pytest.mark.parametrize('hidden', OPTIONAL)
def test_the_registries_load_without_an_optional_package(listed, hidden):
    d = describe(hidden)
    for kind in ('solvers', 'analyses'):
        assert ([r['name'] for r in d[kind]]
                == [r['name'] for r in listed[kind]])
    checks = {r['name']: r['check'] for r in d['solvers'] + d['analyses']}
    for name in NEEDS.get(hidden, ()):
        assert not checks[name][0], name
    if hidden == 'skrf':
        assert 'scikit-rf' in checks['scikit'][1]
