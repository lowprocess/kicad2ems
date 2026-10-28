"""
The example boards, installed with kicad2ems as the package kicad2ems.examples (this folder of the repository): a
config for each (<name>.toml), the generators of the boards that are not in it (make_rf_examples.py,
make_antennas.py, make_demo_board.py), and the three boards that are. Front ends reach them through
kicad2ems.api.frontend:

    examples()                  {name: config path} of every example, in this installed folder
    prepare_example(name, dest) the example in a folder of the user's (default: user_folder()): its config, and its
                                board, copied or generated there; returns the config's path there

The installed folder may not be writable (a system install) and a run writes next to its config, so a front end
opens an example from the folder prepare_example made.
"""
import glob, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATORS = (('rf_', 'make_rf_examples.py'), ('antenna_', 'make_antennas.py'), ('demo_', 'make_demo_board.py'))
BOARDS = ('hairpin', 'rf_interdig', 'sma_launch')                   # in the repository (pyproject's package-data)


def examples():
    """{name: config path} of every example."""
    return {os.path.basename(p)[:-5]: p for p in sorted(glob.glob(os.path.join(HERE, '*.toml')))}


def user_folder():
    """Where the examples are prepared by default: $XDG_DATA_HOME/kicad2ems/examples (~/.local/share/...)."""
    data = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(data, 'kicad2ems', 'examples')


def board_of(config):
    """The board file a config names (relative to it)."""
    m = re.search(r'^board\s*=\s*"([^"]+)"', open(config).read(), re.M)
    if not m:
        raise ValueError(f"{config}: no board")
    return m.group(1)


def generator(stem):
    """(script, args before the output path, args after it) that makes board <stem>, or None for a board that is
    in the folder itself (BOARDS). A board made earlier in a checkout's folder is not used: it may be stale."""
    if stem in BOARDS:
        return None
    for prefix, script in GENERATORS:
        if stem.startswith(prefix):
            kind = stem[len(prefix):]
            if script == 'make_demo_board.py':                      # make_demo_board.py OUT [VARIANT]
                return script, [], [] if kind == 'transition' else [kind]
            return script, [kind], []
    raise ValueError(f"no generator for board {stem!r}")


def prepare_example(name, dest=None, log=None):
    """The example `name` in folder dest (default user_folder()): its config copied, its board copied (with its
    project and placement files) or generated, unless already there. Returns the config's path in dest."""
    src = examples().get(name)
    if src is None:
        raise KeyError(f"no example {name!r}")
    dest = dest or user_folder()
    os.makedirs(dest, exist_ok=True)
    cfg = os.path.join(dest, name + '.toml')
    shutil.copyfile(src, cfg)                                       # the installed version's config, always
    board = board_of(src)
    if os.path.exists(os.path.join(dest, board)):
        return cfg
    stem = board[:-len('.kicad_pcb')]
    gen = generator(stem)
    if gen is None:
        for p in glob.glob(os.path.join(HERE, stem + '.*')) + glob.glob(os.path.join(HERE, stem + '-*.pos')):
            shutil.copyfile(p, os.path.join(dest, os.path.basename(p)))
    else:
        script, before, after = gen
        if log:
            log(f"making {board} ({script})")
        subprocess.run([sys.executable, os.path.join(HERE, script), *before, os.path.join(dest, stem), *after],
                       check=True, cwd=dest, stdout=subprocess.DEVNULL)
    return cfg
