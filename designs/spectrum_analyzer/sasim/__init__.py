"""A behavioural simulation of a two-LMX2582, two-SIM-83+ spectrum analyzer
(see ../README.md)."""

from . import backend, lo, parts, rf
from .analyzer import Analyzer, Signal

__all__ = ["Analyzer", "Signal", "backend", "lo", "parts", "rf"]
