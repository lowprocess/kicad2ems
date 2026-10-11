"""
kicad2ems - KiCad board -> S-parameters from openEMS (3D FDTD), mom25d (2.5D MoM) and a
transmission-line model (qucsator or scikit-rf), all from one converted board

    kicad2ems board.kicad_pcb --pos board-top.pos --nets SIG,GND --fmin 1 --fmax 6 --preview
    kicad2ems board.toml                        # config with board = "board.kicad_pcb"
    kicad2ems board.toml --solvers mom,lines    # MoM and the line model: no openEMS needed
    kicad2ems board.toml --solvers all          # openEMS, MoM and the line model, compared
    kicad2ems board.kicad_pcb --config board.toml --excite P1
    kicad2ems board.kicad_pcb ... --compare other_tool.s2p          # overlay another result
    kicad2ems --check                           # which solvers this python can run
    kicad2ems --init board.kicad_pcb            # a commented starter config next to the board (board.toml)

Ports are footprints with a Fab-layer text P1, P2, ... (or Reference / Value P<n> in the
pick-and-place file); P<n>+ / P<n>- give a differential port. --preview writes the model + mesh
plot, the transmission-line sections and the report without running openEMS.

Outputs in --out: <name>_<solver>.sNp per solver (openems, mom25d, qucs, scikit), <name>_<solver>.png (the
first solver, the others overlaid), <name>_mesh.png (openEMS; else <name>_model.png), <name>_tlines.png,
<name>_board.json (what every solver gets), <name>_run.json (the run summary), <name>_report.html, the CSX
geometry <name>.xml (AppCSXCAD) and the raw openEMS runs under openems/.
"""
import argparse, os, sys

from .config import ConfigError, build_settings, all_options

GROUPS = dict(solvers='solvers', board='board', band='frequency', output='output')     # command-line group titles


def _help(o):
    d = o.default
    return o.help.replace('{default}', f"{d:g}" if isinstance(d, float) else str(d))


def parser():
    S = argparse.SUPPRESS                        # untyped options stay out: the TOML file can set them
    ap = argparse.ArgumentParser(prog='kicad2ems', description=__doc__, argument_default=S,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true',
                    help='check which solvers this python can run (openEMS, mom25d, the line model) and the GUI, '
                         'then exit')
    ap.add_argument('--init', action='store_true',
                    help='write a commented starter config next to the board (<board>.toml) with the ports found on '
                         'it, nets = auto and the options typed here, then exit')
    ap.add_argument('--config', help='TOML file with any of these options (a solver\'s also under '
                                     '[solver.<name>]); its paths are relative to the file')
    opts = all_options()
    order = [o.group for _, o in opts if o.group != 'output'] + ['output']
    groups = {g: ap.add_argument_group(GROUPS.get(g, g)) for g in dict.fromkeys(order)}
    for _, o in opts:
        extra = {k: v for k, v in (o.cli or {}).items() if k != 'flags'}
        if o.flags == ['board']:
            ap.add_argument('board', help=_help(o), **extra)
            continue
        kw = dict(dest=o.key, help=_help(o))
        if o.type is bool:
            kw['action'] = 'store_true'
        elif o.choices:
            kw['choices'] = list(o.choices)
        elif o.type in (float, int):
            kw['type'] = o.type
        groups[o.group].add_argument(*o.flags, **dict(kw, **extra))
    return ap


def settings_from_argv(argv=None, default_solvers=None):
    """Settings from a command line (the TOML config it names included). Returns (Settings, check flag)."""
    ap = parser()
    typed = vars(ap.parse_args(argv))
    typed.pop('init', None)
    check = typed.pop('check', False)
    config = typed.pop('config', None)
    board = typed.get('board')
    if board and board.lower().endswith('.toml'):
        if config:
            raise ConfigError("two configs: give the .toml either as the argument or with --config")
        config = typed.pop('board')
    if check:
        return None, True
    s = build_settings(typed, config, default_solvers)
    if not s.board:
        raise ConfigError("board file required: as the argument, or 'board = ...' in the config (or --check)")
    return s, False


def check_report():
    """--check: (openEMS usable, lines): openEMS's import check, every solver, the GUI, what each solver models."""
    import importlib
    from .solvers import registry, broken, capability_table
    reg = registry()
    ok_fdtd, lines = reg['openems'].details()
    lines += ['', 'solvers:']
    for name, sv in reg.items():
        ok, what = sv.check()
        lines.append(f"  {'ok' if ok else 'missing':7s} {name:<8s} {sv.label}: {what}")
    lines += [f"  BROKEN  {ep}: {err}" for ep, err in broken]
    try:
        importlib.import_module('PySide6')
        importlib.import_module('pyqtgraph')
        lines.append("  ok      GUI      kicad2ems-gui")
    except ImportError:
        lines.append("  missing GUI      python -m pip install 'kicad2ems[gui]' (PySide6, pyqtgraph)")
    lines += ['', 'what each solver models (docs/architecture.md):'] + capability_table() + ['']
    lines.append("openEMS: ready" if ok_fdtd else "openEMS: NOT usable from this python (without it: --preview, "
                                                  "--solvers mom,lines)")
    return ok_fdtd, lines


def init(argv):
    """--init: the starter config of the board on the command line (the options typed go into it)."""
    from .config import OPTIONS, _convert
    from .starter import write_starter
    typed = {k: v for k, v in vars(parser().parse_args(argv)).items() if k not in ('init', 'check', 'config')}
    board = typed.pop('board', None)
    if not board or board.lower().endswith(('.toml', '.json')):
        raise ConfigError("--init needs the board file: kicad2ems --init board.kicad_pcb")
    path, ports = write_starter(board, {k: _convert(k, v) for k, v in typed.items() if k in OPTIONS})
    print(f"wrote {path}" + (f" ({len(ports)} port(s) found)" if ports else " (no ports found yet: see the file)"))
    return 0


def main(argv=None, prog='kicad2ems', default_solvers=None):
    try:
        if '--init' in (sys.argv[1:] if argv is None else argv):
            return init(sys.argv[1:] if argv is None else argv)
        s, check = settings_from_argv(argv, default_solvers)
        if check:
            ok, lines = check_report()
            print("\n".join(lines))
            return 0 if ok else 2
        for note in s.notes:
            print(f"config: {note}")
        from .pipeline import Job
        return Job(s, command=" ".join([prog] + list(sys.argv[1:] if argv is None else argv))).run()
    except (ConfigError, ValueError, FileNotFoundError, FileExistsError, IsADirectoryError) as e:
        if os.environ.get('KICAD2EMS_TRACEBACK'):              # for debugging: the full traceback
            raise
        msg = e.strerror + f": {e.filename}" if isinstance(e, OSError) and e.filename else str(e)
        print(f"{prog}: error: {msg}", file=sys.stderr)
        return 2


def main_openems(argv=None):
    """The command under its old name, kicad2openems: openEMS unless the solvers are named."""
    return main(argv, prog='kicad2openems', default_solvers=['openems'])


if __name__ == '__main__':
    sys.exit(main())
