#!/bin/bash
# kicad2ems setup: a virtual environment in this directory (.venv) with kicad2ems and the parts you choose.
#
#   ./setup.sh              asks about each optional part (defaults in [brackets]; Enter takes them)
#   ./setup.sh --yes        takes every default without asking
#   ./setup.sh --python python3.12 --venv /path/to/venv
#
# Asks about: the GUI, field dumps, the scikit-rf line model, the gmsh mesher, mom25d's compiled core, the test
# tools, EMerge (3D FEM: from git, patched for Python 3.14, or PyPI); the CuPy wheel for your GPU (detected: NVIDIA CUDA 12 / 13, AMD ROCm 7); openEMS (build a local copy
# into .openems/ with tools/build_openems.sh, use one already installed, or none). Run it again to change
# anything: an existing .venv is kept and added to (or made anew if you say so).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV=$REPO/.venv
PY=${PYTHON:-python3}
YES=0
OEMS_PREFIX=$REPO/.openems
TRIANGLE_COMMIT=595b43eb6682992a0b1012b9671bf860c0e6ae56    # triangle 20250106, builds on Python 3.14
while [ $# -gt 0 ]; do
    case $1 in
        -y|--yes) YES=1 ;;
        --python) PY=$2; shift ;;
        --venv) VENV=$(realpath -m "$2"); shift ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (--help)"; exit 2 ;;
    esac
    shift
done

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }

ask() {     # ask "question" y|n -> 0 for yes
    local q=$1 def=$2 a
    if [ $YES = 1 ]; then [ "$def" = y ]; return; fi
    while true; do
        read -r -p "  $q [$([ "$def" = y ] && echo Y/n || echo y/N)] " a || a=
        a=${a:-$def}
        case ${a,,} in y|yes) return 0 ;; n|no) return 1 ;; esac
    done
}

choose() {  # choose "question" default-index option... -> sets CHOICE (the option's text)
    local q=$1 def=$2 i a
    shift 2
    local opts=("$@")
    if [ $YES = 0 ]; then
        echo "  $q"
        for i in "${!opts[@]}"; do
            printf '    %d) %s%s\n' $((i + 1)) "${opts[$i]}" "$([ $((i + 1)) = "$def" ] && echo '   (default)')"
        done
    fi
    while true; do
        if [ $YES = 1 ]; then a=$def; else read -r -p "  choice [$def] " a || a=; a=${a:-$def}; fi
        if [[ $a =~ ^[0-9]+$ ]] && [ "$a" -ge 1 ] && [ "$a" -le ${#opts[@]} ]; then
            CHOICE=${opts[$((a - 1))]}
            return
        fi
    done
}

# ---- Python and the virtual environment -------------------------------------------------------------
bold "Python"
command -v "$PY" >/dev/null || { echo "  $PY not found (--python to name another)"; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    echo "  kicad2ems needs Python 3.10 or newer; $PY is $("$PY" -V 2>&1)"; exit 1; }
note "$("$PY" -V) ($(command -v "$PY"))"

# openEMS first: an existing install outside the venv is only seen with --system-site-packages
bold "openEMS (3D FDTD; not on PyPI)"
SYS_OEMS=0
"$PY" -c 'import CSXCAD, openEMS' 2>/dev/null && SYS_OEMS=1
if [ $SYS_OEMS = 1 ]; then
    note "this Python already has openEMS: $("$PY" -c 'import openEMS, os; print(os.path.dirname(openEMS.__file__))')"
    choose "openEMS for kicad2ems:" 1 "use that install (the venv sees the system packages)" \
        "build a local copy into .openems/" "none (mom25d and the line model only)"
else
    note "this Python has no openEMS (import CSXCAD, openEMS failed). One in another venv: install kicad2ems"
    note "there instead (<venv>/bin/pip install -e $REPO), or build a local copy here"
    choose "openEMS for kicad2ems:" 3 "use an install of this Python (none found)" \
        "build a local copy into .openems/ (10-20 min, needs the build dependencies)" \
        "none (mom25d and the line model only)"
fi
case $CHOICE in
    use*) OEMS=system ;;
    build*) OEMS=build ;;
    *) OEMS=none ;;
esac
if [ $OEMS = system ] && [ $SYS_OEMS = 0 ]; then
    note "no openEMS install found: going on without (run setup.sh again to build one)"
    OEMS=none
fi

bold "Virtual environment: $VENV"
SITE=()
[ $OEMS = system ] && SITE=(--system-site-packages)
if [ -x "$VENV/bin/python" ]; then
    if ask "it exists: make it anew (otherwise it is kept and added to)?" n; then
        rm -rf "$VENV"
    fi
fi
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv ${SITE[@]+"${SITE[@]}"} "$VENV"
    note "made with $("$VENV/bin/python" -V)${SITE:+, seeing the system packages (openEMS)}"
fi
VPY=$VENV/bin/python
"$VPY" -m pip install -q --upgrade pip setuptools wheel

# ---- optional parts ---------------------------------------------------------------------------------
bold "Optional parts"
EXTRAS=()
ask "GUI (kicad2ems-gui: PySide6, pyqtgraph, PyOpenGL for the 3D field view)?" y && EXTRAS+=(gui)
ask "frequency-domain field dumps and far fields (h5py)?" y && EXTRAS+=(dumps)
ask "line model with scikit-rf (--fast skrf; qucsator is a separate program)?" y && EXTRAS+=(fast)
ask "gmsh mesher for mom25d (mom_mesher = \"gmsh\")?" n && EXTRAS+=(gmsh)
CORE=0
if command -v g++ >/dev/null || command -v c++ >/dev/null; then
    ask "build mom25d's compiled core (C++ / OpenMP, ~5x faster MoM)?" y && { CORE=1; EXTRAS+=(core); }
else
    note "no C++ compiler (g++): mom25d's compiled core is skipped (NumPy kernels, ~5x slower)"
fi
ask "test tools (pytest)?" n && EXTRAS+=(test)

# ---- EMerge: the optional 3D FEM solver (docs/emerge.md) ---------------------------------------------
choose "EMerge (3D FEM, --solvers emerge)?" 1 \
    "none" \
    "from git, patched for this Python (NumPy 2.4, gmsh 4.15): tools/emerge/install_emerge.sh" \
    "from PyPI (needs Python <= 3.13)"
EMERGE=${CHOICE%% *}
if [ "$EMERGE" = from ] && [[ $CHOICE == *PyPI* ]]; then EMERGE=pypi; elif [ "$EMERGE" = from ]; then EMERGE=git; fi
if [ "$EMERGE" = pypi ] && ! "$PY" -c 'import sys; sys.exit(sys.version_info >= (3, 14))'; then
    note "$("$PY" -V): PyPI's EMerge 2.5+ needs Python <= 3.13 - installing from git instead"
    EMERGE=git
fi

# ---- the GPU: CuPy for mom25d's direct solver -------------------------------------------------------
bold "GPU (mom25d's LU in complex64: CuPy)"
GPU_DEF=0 GPU_WHAT="no GPU stack found"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    CUDA=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9]*\)\..*/\1/p' | head -1) || true
    NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1) || true
    GPU_WHAT="NVIDIA: ${NAME:-a CUDA GPU}, driver CUDA ${CUDA:-?}"
    case $CUDA in 12) GPU_DEF=2 ;; 13) GPU_DEF=3 ;; esac
fi
ROCM=
if [ -r /opt/rocm/.info/version ]; then
    ROCM=$(cut -d. -f1 /opt/rocm/.info/version)
elif command -v rpm >/dev/null && rpm -q rocm-hip >/dev/null 2>&1; then
    ROCM=$(rpm -q --qf '%{VERSION}' rocm-hip | cut -d. -f1)
elif command -v dpkg-query >/dev/null && dpkg-query -W -f='${Version}' rocm-hip-runtime >/dev/null 2>&1; then
    ROCM=$(dpkg-query -W -f='${Version}' rocm-hip-runtime | cut -d. -f1)
elif command -v hipconfig >/dev/null; then
    ROCM=$(hipconfig --version 2>/dev/null | cut -d. -f1)
fi
if [ -n "$ROCM" ]; then
    # rocminfo lists the CPU as an agent too: the marketing name of the first agent whose device type is GPU
    CARD=$(command -v rocminfo >/dev/null && rocminfo 2>/dev/null | awk -F': *' '
        /Marketing Name:/ {name = $2} /Device Type:/ && $2 ~ /GPU/ {sub(/ +$/, "", name); print name; exit}') || true
    GPU_WHAT="AMD: ${CARD:-a ROCm GPU}, ROCm $ROCM"
    [ "$ROCM" = 7 ] && GPU_DEF=1
fi
note "found: $GPU_WHAT"
[ -n "$ROCM" ] && [ "$ROCM" != 7 ] && note "CuPy 14 has a ROCm wheel for ROCm 7 only (cupy-rocm-7-0); older ROCm: none here"
choose "CuPy wheel:" $((GPU_DEF == 0 ? 4 : GPU_DEF)) \
    "cupy-rocm-7-0   AMD, ROCm 7" "cupy-cuda12x    NVIDIA, CUDA 12" "cupy-cuda13x    NVIDIA, CUDA 13" \
    "none            mom25d on the CPU"
CUPY=${CHOICE%% *}
[ "$CUPY" = none ] && CUPY=
DROP_CUPY=0
if [ -z "$CUPY" ] && [ -x "$VPY" ] && "$VPY" -m pip show -q cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x >/dev/null 2>&1; then
    ask "the venv has a CuPy wheel already: remove it (mom25d on the CPU)?" n && DROP_CUPY=1
fi

# ---- install ----------------------------------------------------------------------------------------
bold "Installing kicad2ems${EXTRAS:+ [$(IFS=,; echo "${EXTRAS[*]}")]} (editable: this checkout's code is what runs)"
SPEC=$REPO
[ ${#EXTRAS[@]} -gt 0 ] && SPEC="$REPO[$(IFS=,; echo "${EXTRAS[*]}")]"
# mom25d's mesher: PyPI has no triangle >= 20230923 for every Python (none for 3.14): build it from its repository
if ! "$VPY" -c 'import triangle' 2>/dev/null && ! "$VPY" -m pip install -q "triangle>=20230923" 2>/dev/null; then
    note "no triangle wheel for $("$VPY" -V): building it from github.com/drufat/triangle (needs a C compiler)"
    "$VPY" -m pip install "triangle @ git+https://github.com/drufat/triangle.git@$TRIANGLE_COMMIT"
fi
# compat: the checkout's path in a .pth inside the venv, ahead of ~/.local on sys.path. The default editable
# finder runs after the path search, so a kicad2ems installed in ~/.local (visible through
# --system-site-packages) was imported instead of this checkout
"$VPY" -m pip install -e "$SPEC" --config-settings editable_mode=compat
WHERE=$(cd / && "$VPY" -c 'import kicad2ems, os; print(os.path.dirname(os.path.dirname(kicad2ems.__file__)))')
if [ "$WHERE" != "$REPO" ]; then
    note "WARNING: this venv imports kicad2ems from $WHERE, not this checkout: remove that copy"
    note "(e.g. $PY -m pip uninstall kicad2ems, outside the venv) and run ./setup.sh again"
fi
if [ -n "$CUPY" ]; then
    bold "Installing $CUPY"
    # one CuPy wheel at a time: another one in the venv would shadow it
    for other in cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x; do
        [ "$other" != "$CUPY" ] && "$VPY" -m pip uninstall -y -q "$other" 2>/dev/null || true
    done
    "$VPY" -m pip install "$CUPY>=14"
elif [ $DROP_CUPY = 1 ]; then
    "$VPY" -m pip uninstall -y cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x 2>/dev/null || true
fi
if [ "$EMERGE" = git ]; then
    bold "Installing EMerge from git (patched)"
    command -v git >/dev/null || { echo "  git is needed for EMerge from git"; exit 1; }
    "$REPO/tools/emerge/install_emerge.sh" "$VPY" "$REPO/.emerge"
elif [ "$EMERGE" = pypi ]; then
    bold "Installing EMerge from PyPI"
    "$VPY" -m pip install "emerge>=2.8"
fi
if [ $CORE = 1 ]; then
    bold "Building mom25d's compiled core"
    (cd "$REPO" && "$VPY" -m mom25d.core.build) || note "the core did not build: mom25d runs on NumPy (see the error above)"
fi

# ---- openEMS from source ----------------------------------------------------------------------------
if [ $OEMS = build ]; then
    bold "Building openEMS into $OEMS_PREFIX"
    missing=()
    for t in cmake git make; do command -v $t >/dev/null || missing+=($t); done
    command -v g++ >/dev/null || missing+=(g++)
    if command -v dnf >/dev/null; then
        DEPS="sudo dnf install gcc-c++ cmake git hdf5-devel vtk-devel CGAL-devel tinyxml-devel boost-devel python3-devel"
    elif command -v apt-get >/dev/null; then
        DEPS="sudo apt install build-essential cmake git libhdf5-dev libvtk9-dev libcgal-dev libtinyxml-dev libboost-all-dev python3-dev"
    else
        DEPS=
    fi
    [ ${#missing[@]} -gt 0 ] && note "missing: ${missing[*]}"
    if [ -n "$DEPS" ]; then
        note "build dependencies: $DEPS"
        if ask "install them now (runs sudo)?" n; then
            $DEPS
        fi
    else
        note "install a C++ compiler, cmake, git and the HDF5, VTK, CGAL, TinyXML and Boost headers first"
    fi
    "$VPY" -m pip install -q cython numpy h5py setuptools
    if ask "build openEMS now (sources in $OEMS_PREFIX/src)?" y; then
        if (export VIRTUAL_ENV=$VENV PATH=$VENV/bin:$PATH SRC=$OEMS_PREFIX/src
            bash "$REPO/tools/build_openems.sh" "$OEMS_PREFIX"); then
            # the venv's activate script finds the libraries; the GUI and kicad2ems started from it too
            if ! grep -q 'kicad2ems: openEMS libraries' "$VENV/bin/activate"; then
                printf '\n# kicad2ems: openEMS libraries (setup.sh)\nexport LD_LIBRARY_PATH="%s/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n' \
                    "$OEMS_PREFIX" >> "$VENV/bin/activate"
            fi
            OEMS=built
        else
            note "the openEMS build failed (see above): kicad2ems is installed without it; fix and run setup.sh again"
        fi
    fi
fi

# ---- what this venv can run -------------------------------------------------------------------------
bold "Check"
[ $OEMS = built ] && export LD_LIBRARY_PATH="$OEMS_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PATH=$VENV/bin:$PATH VIRTUAL_ENV=$VENV "$VENV/bin/kicad2ems" --check || true
[ -n "$CUPY" ] && "$VPY" -c "from mom25d.gpu import describe; print('  ' + describe())" || true
command -v qucsator_rf >/dev/null || command -v qucsator >/dev/null || \
    note "qucsator (the line model's circuit solver) is not installed: scikit-rf stands in for it"
echo
echo "Done. Use it with:"
echo "  source ${VENV/#$REPO\//}/bin/activate"
echo "  kicad2ems examples/demo_transition.toml        # or: kicad2ems-gui"
