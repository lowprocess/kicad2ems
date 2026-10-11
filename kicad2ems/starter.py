"""
kicad2ems --init board.kicad_pcb: a commented starter config next to the board (<stem>.toml), with the ports
found on it, nets = "auto", the band and the options typed with --init.

    path, ports = write_starter('board.kicad_pcb', dict(fmax=6.0))
"""
import glob, os

from .gui.runner import toml_value


def _pos_file(board):
    """A pick-and-place file next to the board: <stem>-top.pos, <stem>-all.pos, <stem>-both.pos (the first found)."""
    stem = os.path.splitext(board)[0]
    return next((p for suffix in ('-top.pos', '-all.pos', '-both.pos', '-top-pos.csv', '-all-pos.csv')
                 for p in glob.glob(stem + suffix)), None)


def _ports(board, pos, typed):
    """(a line per port, the nets line, an error message) of the board resolved with nets = auto."""
    from .board import Board
    from .importers import open_board
    try:
        b = Board(open_board(board, pos=pos), nets=typed.get('nets', ['auto']), pos_file=pos, ground=typed.get('ground'),
                  ports=typed.get('ports'), log=lambda *a: None)
    except (ValueError, OSError) as e:
        return [], '', str(e)
    lines = [f"{p.name}: {p.layer} -> {p.ref_layer or 'gap'}, w {p.w:.3f} mm at ({p.c[0]:.3f}, {-p.c[1]:.3f}) mm, "
             f"Z {p.z0:g} ohm [{p.source}]" for p in b.ports]
    return lines, f"nets (auto): {', '.join(sorted(b.sel))}" if b.sel else 'nets: all copper', None


def write_starter(board, typed):
    """Write <stem>.toml next to the board; returns (its path, the port lines). typed: options given with --init
    (they go into the file). FileExistsError when the file is there already."""
    from .config import OPTIONS
    base = os.path.dirname(os.path.abspath(board))
    path = os.path.splitext(os.path.abspath(board))[0] + '.toml'
    if os.path.exists(path):
        raise FileExistsError(f"{path} exists: edit it, or remove it first")
    pos = typed.get('pos') or _pos_file(board)
    ports, nets, err = _ports(board, pos, typed)
    rel = lambda p: os.path.relpath(os.path.abspath(p), base)
    stem = os.path.basename(path)
    vals = dict(board=rel(board), pos=rel(pos) if pos else None, nets='auto', fmin=OPTIONS['fmin'].default,
                fmax=OPTIONS['fmax'].default, points=OPTIONS['points'].default, out='out')
    vals.update({k: (rel(v) if OPTIONS[k].path and v else v) for k, v in typed.items() if k != 'board'})
    notes = dict(board="paths are relative to this file", pos="pick-and-place file (ports may be listed there)",
                 nets="the nets under the ports plus the ground nets; or a list, e.g. [\"RF\", \"GND\"]", fmin="GHz",
                 out="output folder")
    cmds = [(f"kicad2ems {stem} --preview", "model, mesh, line sections and the report; no openEMS run"),
            (f"kicad2ems {stem}", "the run (solvers: auto, see below)"),
            ("kicad2ems --check", "which solvers this Python can run")]
    w = max(len(c) for c, _ in cmds)
    L = [f"# kicad2ems run configuration for {os.path.basename(board)} (written by kicad2ems --init)"]
    L += [f"#   {c:<{w}s}   # {what}" for c, what in cmds] + ["#"]
    L += ["# ports found:"] + [f"#   {p}" for p in ports + [nets]] if ports else [f"# the board does not resolve yet: {err}"]
    if not ports and 'no port' in err:
        L += ["# Give each port footprint a Fab text P1, P2, ... (P1+ / P1- for a differential port),",
              "# or add ports here: ports = [\"P1=J1.1\", \"P2@40.0,25.0,F.Cu,180\"]"]
    L.append("")
    for k, v in vals.items():
        if v is None or v == []:
            continue
        line = f"{k:<7s} = {toml_value(v)}"
        L.append(f"{line:<34s}# {notes[k]}" if k in notes else line)
    L += ["# solvers = [\"openems\", \"mom25d\", \"lines\"]   # default auto: the full-wave solver estimated cheapest",
          "#                                         # + the line model (kicad2ems --check)", "",
          "[solver.openems]", "# excite = [\"P1\"]                # the ports to drive (default: all)",
          "# fine   = 0.05                  # mm at the copper edges (default: from the narrowest port trace)", "",
          "[solver.mom25d]", "# dmax   = 0.3                   # largest MoM cell along the copper, mm"]
    with open(path, 'w') as fh:
        fh.write("\n".join(L) + "\n")
    return path, ports
