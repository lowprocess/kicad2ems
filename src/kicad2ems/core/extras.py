"""How to get a part kicad2ems knows of but cannot find: the command a message
gives for an extra of pyproject.toml.

The solvers and analyses that were packages of their own (mom25d, pcbqs, gfdtd,
dfsynth: the extras mom, qs, gfdtd, filter, dumps) are in kicad2ems itself since
0.4, so one of them missing is a broken install, not an extra to add."""

# in kicad2ems itself: their extras bring nothing
PARTS = ('mom', 'qs', 'gfdtd', 'filter', 'dumps')


def install_hint(extra):
    """The command (or advice) that installs what `extra` names."""
    if extra in PARTS:
        return ("it is part of kicad2ems: reinstall kicad2ems (python -m pip "
                "install --force-reinstall kicad2ems)")
    if extra == 'openems':
        return ("openEMS is not on PyPI: your distribution's package, or "
                "tools/build_openems.sh")
    return f"python -m pip install 'kicad2ems[{extra}]'"
