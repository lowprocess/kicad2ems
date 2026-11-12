"""
The example boards, installed with kicad2ems as the package kicad2ems.examples (this folder of the repository): a
config for each (<name>.toml), the generators of the boards that are not in it (make_rf_examples.py,
make_antennas.py, make_demo_board.py), and the three boards that are. Front ends reach them through
kicad2ems.api.frontend:

    examples()                  {name: config path} of every example: those in this installed folder and those the
                                installed analyses ship (kicad2ems.analyses: pcbqs's circuits)
    index()                     the same as a menu's entries, in its order: [dict(name, config, group, title, source)],
                                group a path ('RF structures/Filters')
    prepare_example(name, dest) the example in a folder of the user's (default: user_folder()): its config, and its
                                board, copied or generated there; returns the config's path there
    make_boards(folder)         every generated example board made in folder (default: this one; install.py runs it,
                                through make_boards.py, so a checkout's configs run as they are)

The installed folder may not be writable (a system install) and a run writes next to its config, so a front end
opens an example from the folder prepare_example made.
"""
import glob, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATORS = (('rf_', 'make_rf_examples.py'), ('antenna_', 'make_antennas.py'), ('demo_', 'make_demo_board.py'))
BOARDS = ('hairpin', 'rf_interdig', 'sma_launch')                   # in the repository (pyproject's package-data)
# a front end's menu of these examples: (group, its subgroup, the examples there, in order); OTHER: one not listed
GROUPS = (
    ('Antennas', 'Patches and arrays', ('antenna_patch', 'antenna_cp_patch', 'antenna_aperture', 'antenna_array2x2',
                                        'antenna_array2x2_phased', 'antenna_array4x4_phased')),
    ('Antennas', 'Dipoles, loops and monopoles', ('antenna_dipole', 'antenna_bowtie', 'antenna_loop',
                                                  'antenna_monopole', 'antenna_ifa', 'antenna_slot')),
    ('Antennas', 'Directional and wideband', ('antenna_yagi', 'antenna_lpda', 'antenna_vivaldi',
                                              'antenna_vivaldi_antipodal', 'antenna_uwb')),
    ('RF structures', 'Filters', ('rf_steplpf', 'rf_stubnotch', 'rf_radialstub', 'rf_pcoupled', 'rf_combline',
                                  'rf_openloop', 'rf_interdig', 'hairpin', 'rf_shielded', 'rf_dgs', 'rf_ebg')),
    ('RF structures', 'Couplers, dividers and baluns', ('rf_branchline', 'rf_ratrace', 'rf_wilkinson', 'rf_coupler',
                                                        'rf_marchand')),
    ('RF structures', 'Lines and transitions', ('rf_gcpw', 'rf_stripline', 'rf_siw', 'rf_slotline', 'rf_ms2cpw',
                                                'rf_msvia', 'rf_diffvia', 'rf_bends', 'rf_serpentine', 'rf_crosstalk',
                                                'rf_crosstalk_guard', 'sma_launch')),
    ('RF structures', 'Printed components', ('rf_idc', 'rf_meander', 'rf_spiral')),
    ('Lumped-element demos', '', ('demo_lowpass', 'demo_highpass', 'demo_bandpass', 'demo_transition')),
)
OTHER = 'Other'
SOURCE = 'kicad2ems'                                                # an index entry of this folder's


def own():
    """{name: config path} of the examples in this folder."""
    return {os.path.basename(p)[:-5]: p for p in sorted(glob.glob(os.path.join(HERE, '*.toml')))}


def shipped():
    """{name: its entry} of the examples the installed analyses ship (Analysis.examples: dict(name, config, group,
    title, files)), each with source = its analysis; one that reuses a name is left out."""
    from kicad2ems.analyses import registry                       # (absolute: a checkout runs examples/ as a
                                                                    # package of its own, make_boards.py)
    out, taken = {}, set(own())
    for a in registry().values():
        try:
            entries = list(a.examples())
        except Exception:                                           # a broken plugin never stops kicad2ems
            continue
        for e in entries:
            if e.get('name') and e['name'] not in taken and os.path.isfile(e.get('config', '')):
                out[e['name']] = dict(e, source=a.name)
                taken.add(e['name'])
    return out


def examples():
    """{name: config path} of every example: this folder's, then those the installed analyses ship."""
    return {**own(), **{n: e['config'] for n, e in shipped().items()}}


def title_of(config):
    """What an example is, from the first comment line of its config ('# kicad2ems example: ...'); '' without."""
    try:
        with open(config, encoding='utf-8') as fh:
            line = fh.readline().strip()
    except OSError:
        return ''
    if not line.startswith('#'):
        return ''
    text = line.lstrip('#').strip()
    return re.sub(r'^\w+\s+(\w+\s+)?(example|run configuration)\s*(:|for)?\s*(the\s+)?', '', text).strip()


def index():
    """Every example as a menu's entry, in the menu's order: [dict(name, config, group, title, source)]. group: a
    path of one or two levels ('RF structures/Filters'); title: what it is, from its config's first line; source:
    'kicad2ems', or the analysis that ships it."""
    mine, out, seen = own(), [], set()
    for group, sub, names in GROUPS:
        for n in names:
            if n in mine and n not in seen:
                seen.add(n)
                out.append(dict(name=n, config=mine[n], group=f"{group}/{sub}" if sub else group,
                                title=title_of(mine[n]), source=SOURCE))
    out += [dict(name=n, config=p, group=OTHER, title=title_of(p), source=SOURCE) for n, p in mine.items()
            if n not in seen]
    for n, e in shipped().items():
        out.append(dict(name=n, config=e['config'], group=e.get('group') or OTHER,
                        title=e.get('title') or title_of(e['config']), source=e['source']))
    return out


def user_folder():
    """Where the examples are prepared by default: $XDG_DATA_HOME/kicad2ems/examples (~/.local/share/...)."""
    data = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(data, 'kicad2ems', 'examples')


def board_of(config, needed=True):
    """The board file a config names (relative to it); needed=False: None when it names none (an analysis's
    example that brings no board)."""
    m = re.search(r'^board\s*=\s*"([^"]+)"', open(config, encoding='utf-8').read(), re.M)
    if not m:
        if needed:
            raise ValueError(f"{config}: no board")
        return None
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
    theirs = shipped().get(name)
    if theirs is not None:                                          # an analysis's: its files, from its folder
        here = os.path.dirname(os.path.abspath(src))
        board = board_of(src, needed=False)
        for f in theirs.get('files') or ():                         # what the example is made of: always
            if f != board:
                shutil.copyfile(os.path.join(here, f), os.path.join(dest, os.path.basename(f)))
        if board and not os.path.exists(os.path.join(dest, board)): # its board: kept when there, as below
            shutil.copyfile(os.path.join(here, board), os.path.join(dest, board))
        return cfg
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


def make_boards(folder=HERE, names=None, log=print):
    """Every example board that is not in the repository (rf_*, antenna_*, demo_*: .gitignore), made by its
    generator in `folder` next to the configs, made again if there (a generator may have changed since). names:
    only these examples. Returns the boards that failed, {board: the generator's last line of output}."""
    boards = {}
    for name, cfg in own().items():
        if names and name not in names:
            continue
        stem = board_of(cfg)[:-len('.kicad_pcb')]
        if generator(stem) is not None:
            boards.setdefault(stem, name)
    failed = {}
    for i, stem in enumerate(sorted(boards)):
        script, before, after = generator(stem)
        p = subprocess.run([sys.executable, os.path.join(HERE, script), *before, os.path.join(folder, stem), *after],
                           cwd=folder, capture_output=True, text=True)
        if p.returncode:
            failed[stem + '.kicad_pcb'] = ((p.stderr or p.stdout).strip().splitlines() or ['?'])[-1]
        if log:
            log(f"  [{i + 1}/{len(boards)}] {stem}.kicad_pcb" + (f": FAILED ({failed[stem + '.kicad_pcb']})"
                                                                if p.returncode else ''))
    return failed
