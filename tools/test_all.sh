#!/bin/bash
# Every test suite, locally: kicad2ems's own (on the stand-in solvers, tests/standins.py: it needs no solver
# package), then kicad2ems-gui's and each solver package's from its checkout beside this one (kicad2ems-gui,
# kicad2ems-openems, mom25d, gfdtd), which test the window and the kicad2ems plugins against this checkout. A package without a checkout is skipped and said so.
#
#   tools/test_all.sh                       everything that is here
#   tools/test_all.sh --core                kicad2ems's own suite only
#   tools/test_all.sh --openems             also openEMS in the conformance test (slow; KICAD2EMS_CONFORMANCE_OPENEMS)
#   tools/test_all.sh -- -x -k antenna      pytest arguments, to every suite
#
# Python: $PYTHON, else the active venv's, else this checkout's .venv, else python3. Checkouts: KICAD2EMS_GUI_SRC,
# KICAD2EMS_OPENEMS_SRC, MOM25D_SRC, GFDTD_SRC (default ../<name>). The runs never see your settings: a scratch XDG_CONFIG_HOME (the GUI's
# QSettings, site.toml), the GUI offscreen, matplotlib without a display. Exit status: 1 when any suite fails.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
CORE_ONLY=0 ARGS=()
while [ $# -gt 0 ]; do
    case $1 in
        --core) CORE_ONLY=1 ;;
        --openems) export KICAD2EMS_CONFORMANCE_OPENEMS=1 ;;
        --) shift; ARGS=("$@"); break ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option $1 (pytest arguments go after --)" >&2; exit 2 ;;
    esac
    shift
done

if [ -n "${PYTHON:-}" ]; then PY=$PYTHON
elif [ -n "${VIRTUAL_ENV:-}" ]; then PY=$VIRTUAL_ENV/bin/python
elif [ -x "$ROOT/.venv/bin/python" ]; then PY=$ROOT/.venv/bin/python
else PY=python3
fi
"$PY" -c 'import kicad2ems, pytest' 2>/dev/null || { echo "$PY cannot import kicad2ems and pytest: install them (pip install -e '.[test]')" >&2; exit 2; }

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
export XDG_CONFIG_HOME=$SCRATCH/xdg QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg KICAD2EMS_SRC=$ROOT

echo "python: $PY ($("$PY" -V 2>&1))"
RESULTS=() STATUS=0

run_suite() {   # name, folder
    local name=$1 dir=$2 log=$SCRATCH/$1.log
    echo "=== $name ($dir)"
    (cd "$dir" && "$PY" -m pytest -q -p no:cacheprovider ${ARGS[@]+"${ARGS[@]}"}) 2>&1 | tee "$log" | tail -n 25
    local code=${PIPESTATUS[0]}
    local summary
    summary=$(grep -E '^(=+ )?[0-9]+ (passed|failed|error|deselected|skipped)|no tests ran' "$log" | tail -1 | sed 's/=//g; s/^ *//; s/ *$//')
    RESULTS+=("$(printf '%-18s %s' "$name" "${summary:-exit $code}")")
    [ "$code" = 0 ] || [ "$code" = 5 ] || STATUS=1                   # 5: no tests collected (a -k matching none)
}

run_suite kicad2ems "$ROOT"

if [ $CORE_ONLY = 0 ]; then
    for spec in kicad2ems-gui:kicad2ems_gui:KICAD2EMS_GUI_SRC kicad2ems-openems:kicad2ems_openems:KICAD2EMS_OPENEMS_SRC \
                mom25d:mom25d:MOM25D_SRC gfdtd:gfdtd:GFDTD_SRC; do
        IFS=: read -r name module var <<< "$spec"
        dir=${!var:-$(dirname "$ROOT")/$name}
        if [ ! -d "$dir/tests" ]; then
            RESULTS+=("$(printf '%-18s %s' "$name" "skipped: no checkout at $dir ($var)")")
            continue
        fi
        # their tests import the installed package: say so when that is not this checkout
        where=$("$PY" -c "import $module, os; print(os.path.realpath($module.__file__))" 2>/dev/null)
        if [ -z "$where" ]; then
            RESULTS+=("$(printf '%-18s %s' "$name" "skipped: $module is not installed in $PY (pip install -e $dir)")")
            continue
        fi
        case $where in
            "$(realpath "$dir")"/*) ;;
            *) echo "WARNING: $name: $PY imports $where, not the checkout at $dir (pip install -e $dir)" ;;
        esac
        run_suite "$name" "$dir"
    done
fi

echo
echo "=== summary"
printf '%s\n' "${RESULTS[@]}"
exit $STATUS
