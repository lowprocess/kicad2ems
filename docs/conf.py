"""
Sphinx configuration of the kicad2ems documentation site (Read the Docs:
.readthedocs.yaml). The pages are the Markdown files of docs/ (through MyST),
unchanged, so they read the same on GitHub: the site's navigation is added to
index.md as it is read (NAV). The code reference (reference/) covers every part
of kicad2ems: autodoc for the Python, Doxygen through Breathe for the compiled
cores of mom25d and gfdtd.

    python -m pip install -r docs/requirements.txt
        # Sphinx, MyST, Furo, Breathe
    sphinx-build -b html docs docs/_build/html  # needs doxygen on PATH

kicad2ems is imported from where it is installed (a checkout's editable install,
or Read the Docs' pip install of this checkout), else from src/.
"""
import importlib.util, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, '_build')
# not installed: this checkout's (its examples package exists only installed:
# pip install .)
if importlib.util.find_spec('kicad2ems') is None:
    sys.path.insert(0, os.path.join(ROOT, 'src'))
# the generators import _pcb as a sibling
sys.path.insert(0, os.path.join(ROOT, 'examples'))

import kicad2ems                                                    # noqa: E402

project = 'kicad2ems'
release = version = kicad2ems.__version__
root_doc = 'index'
extensions = [
    'myst_parser', 'sphinx.ext.autodoc', 'sphinx.ext.autosummary',
    'sphinx.ext.viewcode', 'breathe', 'sphinxcontrib.mermaid'
]
# .rst: autosummary's stub pages
source_suffix = {'.md': 'markdown', '.rst': 'restructuredtext'}
exclude_patterns = ['_build']
templates_path = ['_templates']

html_theme = 'furo'
html_title = f'kicad2ems {release}'
html_show_copyright = False
html_static_path = ['_static']
html_css_files = ['kicad2ems.css']  # docstrings wrapped
# #links to headings, as GitHub makes them
myst_heading_anchors = 3
# ```mermaid blocks: diagrams, as on GitHub
myst_fence_as_directive = ['mermaid']
# MyST resolves page.md#x only to headings, and the pages' anchors are HTML (<a
# id="x"></a>, as GitHub needs): its warnings are left out, and _check_links
# checks every link of the built pages instead. mom25d's and gfdtd's cores each
# have functions of the same names (separate programs).
suppress_warnings = ['myst.xref_missing', 'duplicate_declaration.cpp']

autosummary_generate = True
# a package re-exports (kicad2ems.core.importers.kicad: KiCadBoard ...):
# followed to their source, viewcode files a whole module's source page under
# the re-exporting name, and every link back from it misses
viewcode_follow_imported_members = False
autodoc_default_options = dict(members=True, undoc_members=True,
                               show_inheritance=True, member_order='bysource')
# every member, not only a module's __all__ (its API: docs/api.md)
autodoc_default_options['ignore-module-all'] = True
# what a machine may lack (GPU stacks, openEMS, KiCad's IPC client): stand-ins,
# so their importers still import
autodoc_mock_imports = [
    m for m in ('cupy', 'cupyx', 'torch', 'CSXCAD', 'openEMS', 'kipy')
    if importlib.util.find_spec(m) is None
]
# the packages' entry points (python -m <package>) run their program when
# imported, and listing a package's modules imports each: stand-ins, which
# autosummary leaves out (the GUI's would open its window and wait)
autodoc_mock_imports += [
    f'kicad2ems{p}.__main__' for p in ('', '.gui', '.ipc', '.gfdtd', '.dfsynth',
                                       '.emsdev', '.mom25d', '.pcbqs', '.fdtd')
]
# openEMS's own modules, vendored into gfdtd's compat packages: they import
# `openEMS` and `CSXCAD` by those names, which resolve only with the compat
# folder on the path (kicad2ems.gfdtd.compat)
autodoc_mock_imports += [
    f'kicad2ems.gfdtd.compat.openEMS.{m}' for m in ('utilities', 'sar_utils')
]
# these compile their core
autodoc_mock_imports += [
    f'kicad2ems.{p}.core.build' for p in ('mom25d', 'gfdtd', 'pcbqs')
]


def _doxygen():
    """Doxygen's XML of the compiled cores (mom25d's and gfdtd's C++, shipped
    with the package)."""
    if not shutil.which('doxygen'):
        raise RuntimeError(
            "the code reference of the compiled cores needs doxygen on PATH")
    inputs = [
        os.path.join(
            os.path.dirname(importlib.util.find_spec(f'kicad2ems.{p}').origin),
            'core') for p in ('mom25d', 'gfdtd')
    ]
    out = os.path.join(BUILD, 'doxygen')
    os.makedirs(out, exist_ok=True)
    cfg = '\n'.join([
        f'PROJECT_NAME = "kicad2ems compiled cores"',
        f'INPUT = {" ".join(inputs)}',
        'FILE_PATTERNS = *.cpp *.h',
        f'OUTPUT_DIRECTORY = {out}',
        'GENERATE_XML = YES',
        'GENERATE_HTML = NO',
        'GENERATE_LATEX = NO',
        'EXTRACT_ALL = YES',
        'EXTRACT_STATIC = YES',
        'QUIET = YES',
        'WARN_IF_UNDOCUMENTED = NO',
        'ENABLE_PREPROCESSING = YES',
        'MACRO_EXPANSION = YES',
        # gfdtd's device qualifier and template
        'EXPAND_ONLY_PREDEF = YES',
        'PREDEFINED = GF_DEV=inline "GF_TEMPLATE=template<typename C>"'
    ])
    subprocess.run(['doxygen', '-'], input=cfg, text=True, check=True)
    return os.path.join(out, 'xml')


breathe_projects = {'cores': _doxygen()}
breathe_default_project = 'cores'


# the site's navigation, in index.md's order (its tables are the same pages, for
# GitHub)
NAV = [('Using kicad2ems', [
    'getting-started', 'usage', 'gui', 'si', 'timedomain', 'rlgc',
    'segment-studies',
    'kicad-plugin',
    'examples',
    'postprocessing', 'benchmarks', 'solver-matrix'
]),
       ('The solvers',
        ['meshing', 'simulation', 'mom', 'gfdtd', 'transmission-lines']),
       ('The parts', [
           'mom25d/README', 'gfdtd/README', 'fdtd/README', 'fem/README',
           'gui/README', 'ipc/README', 'dfsynth/README', 'emsdev/README',
           'pcbqs/README'
       ]),
       ('pcbqs: power and quasistatics', [
           'pcbqs/power-workflow', 'pcbqs/quasistatic',
           'pcbqs/quasistatic-validation', 'pcbqs/sparams', 'pcbqs/circuit',
           'pcbqs/conducted',
           'pcbqs/thermal', 'pcbqs/static-backends', 'pcbqs/peec-memory',
           'pcbqs/benchmarks/cm5-u702', 'pcbqs/benchmarks/cm5-converter/README',
           'pcbqs/migration'
       ]),
       ('Working on kicad2ems', [
           'architecture', 'api', 'kicad-import', 'model', 'testing',
           'maintenance', 'pcbqs/power-tools-review',
           'pcbqs/ROADMAP', 'pcbqs/CHANGELOG'
       ]), ('Code reference', ['reference/index'])]


def _navigation(app, docname, source):
    """index.md with the site's navigation."""
    if docname == 'index':
        source[0] += ''.join(
            f"\n\n```{{toctree}}\n:hidden:\n:caption: {caption}\n\n"
            + '\n'.join(pages) + "\n```\n" for caption, pages in NAV)


def _as_written(app, what, name, obj, options, lines):
    """The docstrings are plain text laid out by hand (aligned columns, indented
    examples): shown as written."""
    if any(line.strip() for line in lines):
        # not highlighted
        lines[:] = (['.. code-block:: text', '']
                    + ['    ' + line for line in lines] + [''])


def _check_links(app, exception):
    """Every link between the built pages goes to a page there and, with #x, to
    an id on it: a warning otherwise."""
    import glob, re
    from sphinx.util import logging
    if exception or app.builder.format != 'html':
        return
    out, ids, links = app.outdir, {}, []
    for path in glob.glob(os.path.join(out, '**', '*.html'), recursive=True):
        if os.path.relpath(path, out).startswith('_static'):
            # pages copied as they are (the solver matrix: its ids are made by
            # its script)
            continue
        page = open(path, encoding='utf-8').read()
        ids[os.path.normpath(path)] = set(re.findall(r'\bid="([^"]+)"', page))
        links += [(path, h)
                  for h in re.findall(r'<a [^>]*href="([^"]+)"', page)]
    log = logging.getLogger(__name__)
    for path, href in links:
        if re.match(r'[a-z]+:', href) or href.startswith('_'):
            continue  # another site, or Sphinx's own files
        target, _, frag = href.partition('#')
        dest = (os.path.normpath(os.path.join(os.path.dirname(path), target))
                if target else os.path.normpath(path))
        if dest not in ids:
            # its index lists modules without source
            viewcode = os.path.relpath(path, out).startswith('_modules')
            if (not viewcode and not target.startswith(('_', '../_'))
                    and target.endswith('.html')):
                log.warning(
                    f"{os.path.relpath(path, out)}: link to {href}: no such "
                    "page"
                )
        elif frag and frag not in ids[dest]:
            log.warning(
                f"{os.path.relpath(path, out)}: link to {href}: no id {frag!r} "
                "there"
            )


def setup(app):
    app.connect('build-finished', _check_links)
    app.connect('source-read', _navigation)
    app.connect('autodoc-process-docstring', _as_written)
