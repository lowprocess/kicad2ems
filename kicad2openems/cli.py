"""
kicad2openems - KiCad board -> S-parameters from openEMS (3D FDTD), mom25d (2.5D MoM) and a
transmission-line model, all from one converted board

    kicad2openems board.kicad_pcb --pos board-top.pos --nets SIG,GND --fmin 1 --fmax 6 --preview
    kicad2openems board.toml                        # config with board = "board.kicad_pcb"
    kicad2openems board.toml --solvers mom,lines    # MoM and the line model: no openEMS needed
    kicad2openems board.toml --solvers all          # openEMS, MoM and the line model, compared
    kicad2openems board.kicad_pcb --config board.toml --excite P1
    kicad2openems board.kicad_pcb ... --compare other_tool.s2p          # overlay another result
    kicad2openems --check                           # which solvers this python can run

Ports are footprints with a Fab-layer text P1, P2, ... (or Reference / Value P<n> in the
pick-and-place file); P<n>+ / P<n>- give a differential port. --preview writes the model + mesh
plot, the transmission-line sections and the report without running openEMS.

Outputs in --out: <name>_openems.sNp / _mom.sNp / _fast.sNp (per solver), <name>_openems.png,
<name>_mesh.png, <name>_tlines.png, <name>_openems.json (the run summary), <name>_report.html, the CSX
geometry <name>.xml (AppCSXCAD) and the raw openEMS runs under openems/.
"""
import argparse, os, sys

from .config import Settings, ConfigError, build_settings

D = Settings()                                   # defaults, quoted in the help


def parser():
    S = argparse.SUPPRESS                        # untyped options stay out: the TOML file can set them
    ap = argparse.ArgumentParser(prog='kicad2openems', description=__doc__, argument_default=S,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board', nargs='?', help='.kicad_pcb, or a .toml config (that names the board)')
    ap.add_argument('--check', action='store_true',
                    help='check that this python can run openEMS (and mom25d, the line model, the GUI), then exit')
    ap.add_argument('--config', help='TOML file with any of these options (openEMS ones also under [openems]); '
                                     'its paths are relative to the file')
    g = ap.add_argument_group('solvers')
    g.add_argument('--solvers', metavar='LIST',
                   help='solvers to run, comma-separated: openems (3D FDTD; the default), mom (mom25d, 2.5D MoM: '
                        'planar boards over a ground plane, no openEMS needed), lines (the transmission-line model '
                        'as a circuit: milliseconds) or all. A named solver that cannot run stops the run before it '
                        'starts; --mom and --fast add theirs as references')
    g = ap.add_argument_group('board')
    g.add_argument('--pos', help='pick-and-place file listing the port components')
    g.add_argument('--nets', help='comma-separated nets to include, or auto (the nets under the ports plus the '
                                  'ground nets; default: all copper)')
    g.add_argument('--region', help='crop to x0,y0,x1,y1 (KiCad mm) or to the shapes on a drawing layer (e.g. User.1); '
                                    'in a config also a list of [x, y] points. Ports outside it are left out')
    g.add_argument('--port', dest='ports', action='append', metavar='SPEC',
                   help='an extra port: P3=J1.1 (on pad 1 of J1) or P3@x,y[,layer[,rotation]] (KiCad mm); repeatable')
    g.add_argument('--cut-ports', action='store_true', help='a port wherever a signal track crosses the region edge')
    g.add_argument('--ground', help='copper layer to model as a solid plane over the whole outline')
    g.add_argument('--mask', action='store_true', help='include solder mask layers (thin: smaller time step)')
    g.add_argument('--parts', choices=['auto', 'none'],
                   help='lumped R / L / C parts between two pads on the modelled copper (default auto)')
    g = ap.add_argument_group('frequency')
    g.add_argument('--fmin', type=float, help=f'GHz (default {D.fmin:g})')
    g.add_argument('--fmax', type=float, help=f'GHz (default {D.fmax:g})')
    g.add_argument('--points', type=int, help=f'frequency points (default {D.points})')
    g = ap.add_argument_group('mesh')
    g.add_argument('--res', type=float, help=f'cells per wavelength at fmax in each medium (default {D.res:g})')
    g.add_argument('--fine', type=float, help='mm, cell size at copper edges (default: the narrowest port trace '
                                              'or twice the thinnest dielectric, whichever is smaller, / 4)')
    g.add_argument('--air', type=float, help='mm of air between the board and the boundary (default 5; '
                                             'antenna mode: a quarter wavelength at the band centre, at least 5)')
    g.add_argument('--ratio', type=float, help=f'max growth between neighbouring cells (default {D.ratio:g})')
    g.add_argument('--nz', type=int, help=f'minimum cells through each dielectric layer (default {D.nz})')
    g.add_argument('--curves', choices=['extremes', 'vertices'],
                   help='mesh lines for rounded corners / arcs / circles: only the ends and extremes of each curve '
                        '(default) or every vertex')
    g.add_argument('--vias', choices=['auto', 'exact', 'centre', 'snap'],
                   help='via mesh lines: auto = exact on signal nets, snap elsewhere; exact = centre and +-radius; '
                        'centre = one line per axis; snap = move onto existing lines (within --via-snap)')
    g.add_argument('--via-snap', type=float, help=f'mm a snapped via may move (default {D.via_snap:g})')
    g.add_argument('--edge-walls', metavar='SIDES',
                   help='PEC walls where the ground planes end at these board sides, standing in for stitching vias '
                        'or edge plating: xmin,xmax,ymin,ymax (KiCad coordinates: ymin = top) or all; '
                        'kept --edge-keepout clear of signal copper')
    g.add_argument('--edge-keepout', type=float, help=f'mm between edge walls and signal copper (default {D.edge_keepout:g})')
    g.add_argument('--converge', metavar='F1,F2,...',
                   help='mesh convergence check: run on several mesh levels, level f with --res x f and --fine / f '
                        '(e.g. 1,1.5,2); the finest level gives the result')
    g.add_argument('--converge-tol', metavar='DB,DEG',
                   help='converged when the second-finest level is within this of the finest (default 0.1 dB, 2 deg)')
    g = ap.add_argument_group('solver')
    g.add_argument('--bc', help='boundaries: auto (PEC on a solid bottom plane, MUR elsewhere), MUR, PML_8 (any '
                                'PML_n), PEC, PMC, or six values xmin,xmax,ymin,ymax,zmin,zmax')
    g.add_argument('--end-db', type=float, help=f'stop when the field energy has decayed by this much '
                                                f'(default {D.end_db:g})')
    g.add_argument('--end-on', choices=['ports', 'energy'],
                   help='ports (default): stop when the port signals have decayed by --end-db, or the field energy '
                        'has; energy: only the field energy (openEMS\'s own criterion)')
    g.add_argument('--max-steps', type=int,
                   help=f'timestep limit per run (default {D.max_steps}); low frequencies and high-Q circuits need more')
    g.add_argument('--sigma', type=float, help='copper conductivity S/m (conducting sheets; default PEC)')
    g.add_argument('--diel', choices=['fixed', 'wideband'],
                   help='dielectric loss: fixed = conductivity fitted at the band centre (default); wideband = '
                        'Djordjevic-Sarkar model (er / tand constant-ish over the band) as Debye poles')
    g.add_argument('--diel-fref', type=float, help='GHz at which the stackup er / tand hold (wideband; default 1)')
    g.add_argument('--excite', help='comma-separated ports to excite (default all), e.g. P1')
    g.add_argument('--threads', type=int,
                   help='openEMS threads per run (default 0 = all cores; with --jobs above 1: half the cores, '
                        'shared by the runs)')
    g.add_argument('--jobs', type=int, help='excitations run in parallel (separate processes; default 1)')
    g = ap.add_argument_group('antenna mode')
    g.add_argument('--mode', choices=['sparams', 'antenna'],
                   help='sparams (default): S-parameters of all ports; antenna: one driven port, PML on all '
                        'sides, far field (gain, efficiency, patterns)')
    g.add_argument('--ff-freq', metavar='GHz,...', help='antenna: frequency for the patterns (default: the '
                                                        'resonance, minimum |S11|); also recorded')
    g.add_argument('--ff-step', type=float, help='antenna: angle step of the pattern cuts (deg, default 2)')
    g = ap.add_argument_group('field dumps')
    g.add_argument('--dump', action='append', metavar='FIELD@REGION[@GHz,...]',
                   help='field dump, repeatable: E|H|J @ layer[+-mm] | x=/y=/z= | volume | box=x0,y0,z0,x1,y1,z1 '
                        '[@ frequencies in GHz -> frequency domain + PNG maps; none -> time domain VTK]. '
                        'e.g. E@F.Cu+0.05@2,6   J@In2.Cu@4   E@y=7')
    g.add_argument('--dump-port', help='ports whose excitation runs get the dumps (default: the first excited port)')
    g = ap.add_argument_group('transmission-line model')
    g.add_argument('--fast', choices=['auto', 'skrf', 'qucsator'],
                   help='also solve the transmission-line sections as a circuit (milliseconds) with scikit-rf or '
                        'qucsator (auto: qucsator if installed, else scikit-rf); written as <name>_fast.sNp and '
                        'compared with the other solvers in the report')
    g.add_argument('--qucsator', metavar='PATH', help='qucsator executable (default: qucsator_rf or qucsator on PATH)')
    g = ap.add_argument_group('MoM reference (mom25d)')
    g.add_argument('--mom', action='store_true',
                   help='also solve the CSX geometry (<name>.xml, the one openEMS gets) with the 2.5D MoM solver mom25d: '
                        '<name>_mom.sNp, compared with openEMS and the line model; works with --preview')
    g.add_argument('--mom-dmax', type=float, metavar='MM', help='largest MoM cell along the copper (default 0.3)')
    g.add_argument('--mom-port', choices=['auto', 'wave', 'lumped'],
                   help='auto (default): de-embedded wave ports where a straight feed allows, else lumped')
    g.add_argument('--mom-mesher', choices=['triangle', 'gmsh'], help='MoM surface mesher (default triangle)')
    g.add_argument('--mom-solves', type=int, metavar='N', help='most EM solves of the adaptive sweep (default 60)')
    g = ap.add_argument_group('output')
    g.add_argument('--compare', help='Touchstone file to overlay on the result plot')
    g.add_argument('--preview', action='store_true', help='write the model/mesh preview and the report, then exit')
    g.add_argument('--no-report', action='store_true', help='do not write <name>_report.html')
    g.add_argument('--openems-output', action='store_true',
                   help="show openEMS's own output instead of the progress / ETA line (it is always kept in "
                        "openems.log of each run)")
    g.add_argument('--events', metavar='FILE', help='also write the run as JSON lines (stages, log, progress, files) '
                                                    'to FILE, for a GUI or a script')
    g.add_argument('--out', help=f'output folder (default {D.out})')
    return ap


def settings_from_argv(argv=None):
    """Settings from a command line (the TOML config it names included). Returns (Settings, check flag)."""
    ap = parser()
    typed = vars(ap.parse_args(argv))
    check = typed.pop('check', False)
    config = typed.pop('config', None)
    board = typed.get('board')
    if board and board.lower().endswith('.toml'):
        if config:
            raise ConfigError("two configs: give the .toml either as the argument or with --config")
        config = typed.pop('board')
    if check:
        return None, True
    s = build_settings(typed, config)
    if not s.board:
        raise ConfigError("board file required: as the argument, or 'board = ...' in the config (or --check)")
    return s, False


def other_solvers():
    """--check lines for what runs without openEMS: mom25d, the line model's circuit solvers, the GUI."""
    import importlib, shutil
    from .mom import mom25d_status
    lines = ["other solvers (no openEMS needed):"]
    ok, what = mom25d_status()
    lines.append(f"  {'ok' if ok else 'FAILED':7s} mom25d      {what}")
    try:
        skrf = importlib.import_module('skrf')
        lines.append(f"  ok      scikit-rf   {getattr(skrf, '__version__', '')} (line model: --fast skrf)")
    except ImportError:
        lines.append("  missing scikit-rf   python -m pip install scikit-rf (line model: --fast skrf)")
    q = shutil.which('qucsator_rf') or shutil.which('qucsator')
    lines.append(f"  ok      qucsator    {q} (line model: --fast qucsator)" if q else
                 "  missing qucsator    optional: Qucs-S's qucsator_rf on PATH (line model with coupled lines, "
                 "discontinuities)")
    try:
        importlib.import_module('PySide6')
        importlib.import_module('pyqtgraph')
        lines.append("  ok      GUI         kicad2openems-gui")
    except ImportError:
        lines.append("  missing GUI         python -m pip install 'kicad2openems[gui]' (PySide6, pyqtgraph)")
    return lines


def main(argv=None):
    try:
        s, check = settings_from_argv(argv)
        if check:
            from .simulation import check_openems
            ok, report = check_openems()
            print("\n".join(report + [''] + other_solvers() + ['']))
            print("openEMS: ready" if ok else "openEMS: NOT usable from this python (without it: --preview, "
                                              "--solvers mom,lines)")
            return 0 if ok else 2
        for note in s.notes:
            print(f"config: {note}")
        from .pipeline import Job
        return Job(s, command=" ".join(['kicad2openems'] + list(sys.argv[1:] if argv is None else argv))).run()
    except (ConfigError, ValueError, FileNotFoundError, IsADirectoryError) as e:
        if os.environ.get('KICAD2OPENEMS_TRACEBACK'):              # for debugging: the full traceback
            raise
        msg = e.strerror + f": {e.filename}" if isinstance(e, OSError) and e.filename else str(e)
        print(f"kicad2openems: error: {msg}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
