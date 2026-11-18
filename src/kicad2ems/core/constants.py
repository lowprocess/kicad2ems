"""
Physical constants (SI), the one definition of each in kicad2ems.
"""
import math

__all__ = ['C0', 'EPS0', 'MU0', 'ETA0']

C0 = 299792458.0
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * math.pi
ETA0 = 376.730313668                    # the free-space wave impedance
