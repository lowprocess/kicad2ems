"""
Sphinx configuration of the kicad2ems documentation site (Read the Docs: .readthedocs.yaml). The pages are the
Markdown files of docs/ (through MyST), unchanged, so they read the same on GitHub: the site's navigation is added to index.md as
it is read (NAV).
The code reference (reference/) covers the whole stack from the installed packages: autodoc for the Python,
Doxygen through Breathe for the compiled cores of mom25d and gfdtd.

    python -m pip install -r docs/requirements.txt        # Sphinx, MyST, Furo, Breathe
    sphinx-build -b html docs docs/_build/html            # needs doxygen on PATH

The stack's packages are imported from where they are installed (a checkout's editable installs, or Read the
Docs' pip installs of their main branches). The KiCad plugin is not a pip package: it comes from a checkout of
kicad2ems-ipc beside this one, else from its repository's archive.
"""
import importlib.util, io, os, shutil, subprocess, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, '_build')
if importlib.util.find_spec('kicad2ems') is None:                    # not installed: this checkout's (its examples
    sys.path.insert(0, os.path.join(ROOT, 'src'))                   # package exists only installed: pip install .)
sys.path.insert(0, os.path.join(ROOT, 'examples'))                  # the generators import _pcb as a sibling

import kicad2ems                                                    # noqa: E402

project = 'kicad2ems'
release = version = kicad2ems.__version__
root_doc = 'index'
extensions = ['myst_parser', 'sphinx.ext.autodoc', 'sphinx.ext.autosummary', 'sphinx.ext.viewcode', 'breathe',
              'sphinxcontrib.mermaid']
source_suffix = {'.md': 'markdown', '.rst': 'restructuredtext'}        # .rst: autosummary's stub pages
exclude_patterns = ['_build']
templates_path = ['_templates']

html_theme = 'furo'
html_title = f'kicad2ems {release}'
html_show_copyright = False
html_static_path = ['_static']
html_css_files = ['kicad2ems.css']                                 # docstrings wrapped
myst_heading_anchors = 3                                            # #links to headings, as GitHub makes them
myst_fence_as_directive = ['mermaid']                               # ```mermaid blocks: diagrams, as on GitHub
# MyST resolves page.md#x only to headings, and the pages' anchors are HTML (<a id="x"></a>, as GitHub needs):
# its warnings are left out, and _check_links checks every link of the built pages instead. mom25d's and gfdtd's
# cores each have functions of the same names (separate programs).
suppress_warnings = ['myst.xref_missing', 'duplicate_declaration.cpp']

autosummary_generate = True
autodoc_default_options = dict(members=True, undoc_members=True, show_inheritance=True, member_order='bysource')
# what a machine may lack (GPU stacks, openEMS, KiCad's IPC client): stand-ins, so their importers still import
autodoc_mock_imports = [m for m in ('cupy', 'cupyx', 'torch', 'CSXCAD', 'openEMS', 'kipy')
                        if importlib.util.find_spec(m) is None]
# the packages' entry points (python -m <package>) run their program when imported, and listing a package's modules
# imports each: stand-ins, which autosummary leaves out (the GUI's would open its window and wait)
autodoc_mock_imports += [f'{p}.__main__' for p in ('kicad2ems', 'kicad2ems_gui', 'kicad2ems_ipc', 'gfdtd', 'dfsynth',
                                                   'emsdev', 'mom25d', 'kicad2ems_fdtd')]
autodoc_mock_imports += ['mom25d.core.build', 'gfdtd.core.build']        # these compile their core when imported


def _ipc_plugin():
    """The folder holding kicad2ems_ipc: a checkout beside this one, else the repository's archive."""
    local = os.path.join(os.path.dirname(ROOT), 'kicad2ems-ipc', 'plugin')
    if os.path.isdir(os.path.join(local, 'kicad2ems_ipc')):
        return local
    dest = os.path.join(BUILD, 'kicad2ems-ipc')
    if not os.path.isdir(dest):
        url = 'https://github.com/lowprocess/kicad2ems-ipc/archive/refs/heads/main.zip'
        with urllib.request.urlopen(url, timeout=60) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(dest)
    return os.path.join(dest, 'kicad2ems-ipc-main', 'plugin')


sys.path.insert(0, _ipc_plugin())


def _doxygen():
    """Doxygen's XML of the compiled cores (mom25d's and gfdtd's C++, shipped with the packages)."""
    if not shutil.which('doxygen'):
        raise RuntimeError("the code reference of the compiled cores needs doxygen on PATH")
    inputs = [os.path.join(os.path.dirname(importlib.util.find_spec(p).origin), 'core') for p in ('mom25d', 'gfdtd')]
    out = os.path.join(BUILD, 'doxygen')
    os.makedirs(out, exist_ok=True)
    cfg = '\n'.join([f'PROJECT_NAME = "kicad2ems compiled cores"', f'INPUT = {" ".join(inputs)}',
                     'FILE_PATTERNS = *.cpp *.h', f'OUTPUT_DIRECTORY = {out}', 'GENERATE_XML = YES',
                     'GENERATE_HTML = NO', 'GENERATE_LATEX = NO', 'EXTRACT_ALL = YES', 'EXTRACT_STATIC = YES',
                     'QUIET = YES', 'WARN_IF_UNDOCUMENTED = NO', 'ENABLE_PREPROCESSING = YES', 'MACRO_EXPANSION = YES',
                     'EXPAND_ONLY_PREDEF = YES', 'PREDEFINED = GF_DEV=inline'])     # gfdtd's device qualifier
    subprocess.run(['doxygen', '-'], input=cfg, text=True, check=True)
    return os.path.join(out, 'xml')


breathe_projects = {'cores': _doxygen()}
breathe_default_project = 'cores'


# the site's navigation, in index.md's order (its tables are the same pages, for GitHub)
NAV = [('Using kicad2ems', ['getting-started', 'usage', 'gui', 'kicad-plugin', 'examples', 'postprocessing', 'benchmarks']),
       ('The solvers', ['meshing', 'simulation', 'mom', 'gfdtd', 'transmission-lines']),
       ('Working on kicad2ems', ['architecture', 'api', 'kicad-import', 'model', 'testing', 'maintenance']),
       ('Code reference', ['reference/index'])]


def _navigation(app, docname, source):
    """index.md with the site's navigation."""
    if docname == 'index':
        source[0] += ''.join(f"\n\n```{{toctree}}\n:hidden:\n:caption: {caption}\n\n" + '\n'.join(pages) + "\n```\n"
                             for caption, pages in NAV)


def _api_names():
    """reference/api-names.md: each name of kicad2ems.api.solver and .frontend (and kicad2ems_gui.api), linked to
    where it is defined (the API modules re-export: an object is documented once, in its own module)."""
    import importlib, inspect
    from kicad2ems.api import solver, frontend
    rows = ['# API names', '', 'Each name of the APIs between the packages ([APIs](../api.md)), and where it is defined.']
    for mod in (solver, frontend):
        rows += ['', f'## `{mod.__name__}`', '', '| Name | Defined as |', '|---|---|']
        for name, spec in mod.NAMES.items():
            target, _, attr = spec.partition(':')
            target = 'kicad2ems' + target if target.startswith('.') else target
            if not attr:
                rows.append(f'| `{name}` | the module {{py:mod}}`{target}` |')
                continue
            obj = getattr(importlib.import_module(target), attr)
            role = 'py:class' if inspect.isclass(obj) else 'py:func' if callable(obj) else 'py:data'
            rows.append(f'| `{name}` | {{{role}}}`{target}.{attr}` |')
    rows += ['', '## `kicad2ems_gui.api`', '', '| Name | Defined as |', '|---|---|',
             '| `PlanView` | {py:class}`kicad2ems_gui.planview.PlanView` |', '']
    path = os.path.join(HERE, 'reference', 'api-names.md')
    text = '\n'.join(rows)
    if not os.path.exists(path) or open(path).read() != text:      # rewritten only when it changes (Sphinx's cache)
        with open(path, 'w') as fh:
            fh.write(text)


_api_names()


def _as_written(app, what, name, obj, options, lines):
    """The docstrings are plain text laid out by hand (aligned columns, indented examples): shown as written."""
    if any(line.strip() for line in lines):
        lines[:] = ['.. code-block:: text', ''] + ['    ' + line for line in lines] + ['']   # not highlighted


def _check_links(app, exception):
    """Every link between the built pages goes to a page there and, with #x, to an id on it: a warning otherwise."""
    import glob, re
    from sphinx.util import logging
    if exception or app.builder.format != 'html':
        return
    out, ids, links = app.outdir, {}, []
    for path in glob.glob(os.path.join(out, '**', '*.html'), recursive=True):
        page = open(path, encoding='utf-8').read()
        ids[os.path.normpath(path)] = set(re.findall(r'\bid="([^"]+)"', page))
        links += [(path, h) for h in re.findall(r'<a [^>]*href="([^"]+)"', page)]
    log = logging.getLogger(__name__)
    for path, href in links:
        if re.match(r'[a-z]+:', href) or href.startswith('_'):
            continue                                                # another site, or Sphinx's own files
        target, _, frag = href.partition('#')
        dest = os.path.normpath(os.path.join(os.path.dirname(path), target)) if target else os.path.normpath(path)
        if dest not in ids:
            viewcode = os.path.relpath(path, out).startswith('_modules')    # its index lists modules without source
            if not viewcode and not target.startswith(('_', '../_')) and target.endswith('.html'):
                log.warning(f"{os.path.relpath(path, out)}: link to {href}: no such page")
        elif frag and frag not in ids[dest]:
            log.warning(f"{os.path.relpath(path, out)}: link to {href}: no id {frag!r} there")


def setup(app):
    app.connect('build-finished', _check_links)
    app.connect('source-read', _navigation)
    app.connect('autodoc-process-docstring', _as_written)
