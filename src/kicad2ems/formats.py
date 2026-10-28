"""
The versions of the files kicad2ems writes for other programs to read (kicad2ems-gui, the KiCad plugin, dfsynth).
Each carries its format's name and version, `format` and `format_version`, and a reader takes a version up to
its own: a newer one says to update kicad2ems. The version goes up when a key is removed or changes its meaning;
a new key needs none. A file without them was written before they were stamped: version 1's layout.
(<name>_board.json has its own, board.FORMAT and VERSION.)

    kicad2ems-run        <name>_run.json, the run summary
    kicad2ems-events     --events: its first line ({"kind": "format", ...}), the stream's
    kicad2ems-sysinfo    python -m kicad2ems.sysinfo's JSON
    kicad2ems-antenna    <name>[_<solver>]_antenna.json, an antenna run's results

    stamp(d, name)       d with its format and version
    check(d, name)       None when this kicad2ems reads d, else why not
    read(path, name)     a JSON file, checked (ValueError when this kicad2ems cannot read it)
"""
import json

FORMATS = {'kicad2ems-run': 1, 'kicad2ems-events': 1, 'kicad2ems-sysinfo': 1, 'kicad2ems-antenna': 1}


def stamp(d, name):
    """d with its format's name and version (format, format_version)."""
    return dict(d, format=name, format_version=FORMATS[name])


def check(d, name):
    """None when this kicad2ems reads d as format `name` (a version up to its own, or no stamp: written before
    them), else why not."""
    f, v = d.get('format'), d.get('format_version', 1)
    if f is None:
        return None
    if f != name:
        return f"not a {name} file ({f})"
    if int(v) > FORMATS[name]:
        return f"{name} format {v} is newer than this kicad2ems reads ({FORMATS[name]}): update kicad2ems"
    return None


def read(path, name):
    """A JSON file of format `name`, checked: ValueError when this kicad2ems cannot read it."""
    with open(path) as fh:
        d = json.load(fh)
    why = check(d, name)
    if why:
        raise ValueError(f"{path}: {why}")
    return d
