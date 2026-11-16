"""mom25d's tests. The kicad2ems plugin's tests (test_kicad2ems*.py) use the shared fixtures and example boards
(tests/core); this folder's own helpers (mom25d_reference) are importable by name."""
import os, sys

os.environ.setdefault('MOM25D_CACHE', 'off')                       # no lines kept between tests (test_linecache.py)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'core'))
sys.path.insert(0, HERE)
from k2e_fixtures import *                                          # noqa: E402,F401,F403
