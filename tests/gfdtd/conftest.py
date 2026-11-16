"""This folder's own helper modules, importable by name (pytest imports the
tests by path)."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
