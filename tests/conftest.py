"""kicad2ems's tests: the shared fixtures (k2e_fixtures.py, which the solver packages' tests use too), on the
stand-in solvers (standins.py) in place of whatever solver packages are installed: core's tests need none of
them. test_conformance.py runs the real ones, in a process of their own."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k2e_fixtures import *                                          # noqa: E402,F401,F403
import standins                                                     # noqa: E402

standins.install()                                                  # before kicad2ems.config is imported
