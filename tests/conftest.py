"""kicad2ems's tests: the shared fixtures (k2e_fixtures.py, which the solver packages' tests use too). Tests marked
mom25d are skipped without mom25d (the mom extra)."""
import importlib.util, os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k2e_fixtures import *                                          # noqa: E402,F401,F403


def pytest_configure(config):
    config.addinivalue_line('markers', 'mom25d: needs mom25d installed (the mom extra); skipped without it')


def pytest_collection_modifyitems(config, items):
    if importlib.util.find_spec('mom25d') is None:
        skip = pytest.mark.skip(reason='mom25d is not installed (the mom extra)')
        for item in items:
            if 'mom25d' in item.keywords:
                item.add_marker(skip)
