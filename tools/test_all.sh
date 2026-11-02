#!/bin/sh
# Every test suite, locally: tools/test_all.py does it (tools/test_all.sh --help); this runs it with python3.
exec "${PYTHON_RUNNER:-python3}" "$(dirname "$0")/test_all.py" "$@"
