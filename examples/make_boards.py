#!/usr/bin/env python3
"""Make every example board that is not in the repository (rf_*, antenna_*, demo_*) here, next to its config, with
its generator: install.py runs this, so kicad2ems examples/<name>.toml works in a fresh checkout.

    python examples/make_boards.py [--quiet] [name ...]    # every generated example, or only these (config names)

--quiet: only the boards that failed, and a line of how many were made.

Exits 1 if a board could not be made (each is tried).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from examples import examples, make_boards                           # noqa: E402  (this folder, as a package)

if __name__ == '__main__':
    quiet = '--quiet' in sys.argv
    names = [a for a in sys.argv[1:] if a != '--quiet'] or None
    unknown = sorted(set(names or ()) - set(examples()))
    if unknown:
        sys.exit(f"make_boards.py: no example {', '.join(unknown)} (examples/<name>.toml)")
    made = []
    failed = make_boards(names=names, log=(lambda line: made.append(line)) if quiet else print)
    for board, why in failed.items():
        print(f"  {board}: FAILED ({why})")
    if quiet:
        print(f"  made {len(made) - len(failed)} example boards" + (f", {len(failed)} failed" if failed else ''))
    sys.exit(1 if failed else 0)
