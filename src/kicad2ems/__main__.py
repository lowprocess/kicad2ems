"""python -m kicad2ems: the command line (kicad2ems.core.cli)."""
import sys
from .core.cli import main

# (imported, as the docs' module index does: nothing run)
if __name__ == '__main__':
    sys.exit(main())
