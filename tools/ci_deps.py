"""The third-party requirements of the kicad2ems stack's checkouts, one per line, for CI (.github/workflows/stack.yml):
each repository's dependencies and the extras named, without the stack's own packages (they are installed from
the checkouts next to each other, --no-deps, so a git URL in one never replaces another's checkout).

    python tools/ci_deps.py ../emsdev ../kicad2ems[test,fast,dumps] ../mom25d[test,core] ... > requirements-ci.txt
"""
import os, re, sys

try:
    import tomllib
except ImportError:                                                 # Python 3.10
    import tomli as tomllib

STACK = {'emsdev', 'kicad2ems', 'mom25d', 'gfdtd', 'dfsynth', 'kicad2ems-gui', 'kicad2ems-openems'}


def name(req):
    return re.split(r'[\s\[<>=!~;@]', req.strip(), maxsplit=1)[0].lower().replace('_', '-')


def requirements(arg):
    path, _, extras = arg.partition('[')
    with open(os.path.join(path, 'pyproject.toml'), 'rb') as fh:
        project = tomllib.load(fh)['project']
    reqs = list(project.get('dependencies', []))
    for extra in filter(None, extras.rstrip(']').split(',')):
        reqs += project.get('optional-dependencies', {})[extra.strip()]
    return reqs


def main(args):
    seen = []
    for arg in args:
        for req in requirements(arg):
            if name(req) not in STACK and req not in seen:
                seen.append(req)
    print('\n'.join(seen))


if __name__ == '__main__':
    main(sys.argv[1:])
