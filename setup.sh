#!/bin/sh
# kicad2ems setup on Linux and macOS: install.py does it all (./setup.sh --help); this runs it with python3, or
# $PYTHON. Windows: setup.cmd, or py install.py.
exec "${PYTHON:-python3}" "$(dirname "$0")/install.py" "$@"
