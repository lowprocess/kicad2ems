"""What every config of the repository means, as JSON: the shipped examples',
the tests' and the older forms' (data/configs). test_config_compat.py runs it
with the real solvers and analyses (not the stand-ins) and compares it with
data/config_compat.json, written before the config reader changed:

    python tests/core/config_compat.py > tests/core/data/config_compat.json

--roundtrip: each config first saved as the GUI saves it (its settings with
the file as the template, its analysis tables and its study), beside itself,
and that read instead.
"""
import dataclasses, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def configs():
    pats = ('examples/*.toml', 'src/kicad2ems/*/circuit/examples/*.toml',
            'src/kicad2ems/*/data/*.toml', 'tests/core/data/configs/*.toml')
    return sorted(os.path.relpath(p, ROOT).replace(os.sep, '/')
                  for pat in pats for p in glob.glob(os.path.join(ROOT, pat)))


def _plain(v):
    """v as JSON, the repository's path as <root>."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return (v.replace(ROOT, '<root>').replace(os.sep, '/')
                if isinstance(v, str) else v)
    return _plain(repr(v))


def _try(f):
    try:
        return _plain(f())
    except Exception as e:
        return f"error {type(e).__name__}: {_plain(str(e))}"


def meaning(path):
    from kicad2ems.core import config, study, analyses
    from kicad2ems.gui import runner

    def settings():
        s = config.build_settings({}, path)
        return dict(dataclasses.asdict(s), notes=s.notes)

    def solvers(s):  # the solvers it runs, the line model's resolved
        from kicad2ems.core.solvers import resolve_lines, LINES
        return [resolve_lines(s) if x == LINES else x
                for x in s.solver_plan()[0]]

    def asked(strict):
        st = study.read_config(path, strict=strict)
        return dict(asked=st.asked, fields=st.fields, said=st.said)

    return dict(
        values=_try(lambda: config.read_toml(path)),
        settings=_try(settings),
        solvers=_try(lambda: solvers(config.build_settings({}, path))),
        study=_try(lambda: asked(True)),
        loose=_try(lambda: asked(False)),
        plan=_try(lambda: [dataclasses.astuple(s) for s in study.plan(
            study.read_config(path))]),
        sets=_try(lambda: analyses.read_sets(path)),
        form=_try(lambda: runner.settings_values(path)),
        extra=_try(lambda: runner.extra_values(path)))


def saved(path):
    """A copy of the config at `path` beside it, as the GUI saves it."""
    from kicad2ems.core import study, analyses
    from kicad2ems.core.runs import update_toml
    from kicad2ems.gui import runner
    copy = path[:-5] + '.saved.toml'
    runner.save_config(copy, runner.extra_values(path)
                       | runner.settings_values(path), template=path)
    update_toml(copy, tables={'analysis': analyses.read_sets(path)})
    if study.load_config(path).get('study'):
        study.write_config(copy, study.read_config(path, strict=False))
    return copy


def main():
    os.chdir(ROOT)
    out = {}
    for p in configs():
        p_abs = os.path.join(ROOT, p)
        if '--roundtrip' not in sys.argv:
            out[p] = meaning(p_abs)
            continue
        copy = saved(p_abs)
        try:
            out[p] = json.loads(json.dumps(meaning(copy)).replace(
                os.path.basename(copy), os.path.basename(p)))
        finally:
            os.remove(copy)
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == '__main__':
    main()
