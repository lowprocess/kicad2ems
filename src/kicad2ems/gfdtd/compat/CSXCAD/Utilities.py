"""CSXCAD.Utilities over gfdtd: the direction helpers scripts and openEMS's
utilities use."""
from kicad2ems.gfdtd.automesh import multi_dirs


def CheckNyDir(ny):
    """0, 1 or 2 of a direction given as an index or 'x' / 'y' / 'z'."""
    if isinstance(ny, str):
        ny = 'xyz'.index(ny.lower())
    ny = int(ny)
    if ny not in (0, 1, 2):
        raise ValueError(f"CheckNyDir: {ny!r} is not a direction")
    return ny


def GetMultiDirs(dirs):
    """[indices] of 'x', 'xy', 'xyz', 'all'."""
    return multi_dirs(dirs)
