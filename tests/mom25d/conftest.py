"""mom25d's tests. The kicad2ems solver's tests (test_kicad2ems*.py) use the
shared fixtures and example boards (tests/core); this folder's own helpers
(mom25d_reference) are importable by name."""
import os, sys

# no lines kept between tests (test_linecache.py)
os.environ.setdefault('MOM25D_CACHE', 'off')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'core'))
sys.path.insert(0, HERE)
from k2e_fixtures import *  # noqa: E402,F401,F403


import pytest  # noqa: E402


@pytest.fixture
def rwg_at_4ghz(monkeypatch):
    """The loop/tree basis's threshold at its former 1e-5: tests of the RWG
    basis's fallbacks (stalls, kept LUs, the via blocks between frequencies) on
    boards whose 4 GHz is below today's 1e-3."""
    from kicad2ems.mom25d import fast, policy
    monkeypatch.setattr(policy, 'LOOP_TREE_BELOW', 1e-5)
    monkeypatch.setattr(fast, 'LOOP_TREE_BELOW', 1e-5)
