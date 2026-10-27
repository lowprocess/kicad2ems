#!/bin/bash
# kicad2ems setup: kicad2ems and the parts you choose, where you choose, with a launcher entry for the GUI.
#
#   ./setup.sh                    asks about each part (defaults in [brackets]; Enter takes them)
#   ./setup.sh --yes              takes every default without asking
#   ./setup.sh --target user      where: checkout (.venv here, the default), user (~/.local, commands on your
#                                 PATH), system (/opt/kicad2ems, /usr/local/bin; runs sudo) or python (into
#                                 the Python itself, pip --user: no venv)
#   ./setup.sh --openems /opt/openems/venv/bin/python    an openEMS installed elsewhere (or: build, none)
#   ./setup.sh --qucsator /usr/bin/qucsator_rf           the line model's circuit solver (or: none)
#   ./setup.sh --python python3.12 --venv /path/to/venv  another Python, another venv (checkout target)
#   ./setup.sh --uninstall --target user                 remove what an install of that target added
#
# Asks about: the GUI, field dumps, the scikit-rf line model, mom25d and gfdtd (and their compiled cores), dfsynth, the test
# tools, the CuPy wheel for your GPU
# (detected: NVIDIA CUDA 12 / 13, AMD ROCm 7), openEMS (one this Python has, one in another venv linked in,
# build one with tools/build_openems.sh, or none), qucsator, and a launcher entry and desktop icon for the GUI.
# Run it again to change anything: an existing venv is kept and added to (or made anew if you say so).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PY=${PYTHON:-python3}
YES=0 TARGET= VENV_ARG= OEMS_ARG= QUCS_ARG= UNINSTALL=0
TRIANGLE_COMMIT=595b43eb6682992a0b1012b9671bf860c0e6ae56    # triangle 20250106, builds on Python 3.14
SYSROOT=${KICAD2EMS_SYSROOT:-}                              # tests: the system target under a scratch root, no sudo
while [ $# -gt 0 ]; do
    case $1 in
        -y|--yes) YES=1 ;;
        --python) PY=$2; shift ;;
        --venv) VENV_ARG=$(realpath -m "$2"); shift ;;
        --target) TARGET=$2; shift ;;
        --openems) OEMS_ARG=$2; shift ;;
        --qucsator) QUCS_ARG=$2; shift ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

askpath() { # askpath "question" default -> sets ANSWER (empty: skipped)
    local a
    if [ $YES = 1 ]; then ANSWER=$2; return; fi
    read -r -p "  $1 [${2:-skip}] " a || a=
    ANSWER=${a:-$2}
}

# ---- where: the target ---------------------------------------------------------------------------------
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONF=${XDG_CONFIG_HOME:-$HOME/.config}
if [ -z "$TARGET" ]; then
    if [ -n "$VENV_ARG" ]; then
        TARGET=checkout
    elif [ $UNINSTALL = 1 ]; then
        echo "--uninstall needs --target (checkout, user, system or python)"; exit 2
    else
        bold "Where to install"
        choose "kicad2ems goes:" 1 \
            "checkout  a venv in this checkout (.venv); run it from here or the launcher entry" \
            "user      for you: a venv in $DATA/kicad2ems, kicad2ems and kicad2ems-gui on your PATH (~/.local/bin)" \
            "system    for every user: a venv in /opt/kicad2ems, commands in /usr/local/bin (runs sudo)" \
            "python    into $PY itself (pip install --user, no venv)"
        TARGET=${CHOICE%% *}
    fi
fi
SUDO= USERFLAG=() EDITABLE=1
APPS=$DATA/applications ICONS=$DATA/icons/hicolor/scalable/apps BIN=
case $TARGET in
    checkout) VENV=${VENV_ARG:-$REPO/.venv}; OEMS_PREFIX=$REPO/.openems; RECORD=$VENV/kicad2ems-installed ;;
    user)     VENV=$DATA/kicad2ems/venv; BIN=$HOME/.local/bin; OEMS_PREFIX=$DATA/kicad2ems/openems
              RECORD=$DATA/kicad2ems/installed ;;
    system)   VENV=$SYSROOT/opt/kicad2ems/venv; BIN=$SYSROOT/usr/local/bin; OEMS_PREFIX=$SYSROOT/opt/kicad2ems/openems
              APPS=$SYSROOT/usr/share/applications ICONS=$SYSROOT/usr/share/icons/hicolor/scalable/apps
              RECORD=$SYSROOT/opt/kicad2ems/installed EDITABLE=0
              [ -z "$SYSROOT" ] && [ "$(id -u)" != 0 ] && SUDO=sudo ;;
    python)   VENV=; USERFLAG=(--user); OEMS_PREFIX=$DATA/kicad2ems/openems; RECORD=$DATA/kicad2ems/installed-python ;;
    *) echo "unknown --target $TARGET: checkout, user, system or python"; exit 2 ;;
esac

record() {  # a file or link this install made: removed again by --uninstall
    $SUDO mkdir -p "$(dirname "$RECORD")"
    echo "$1" | $SUDO tee -a "$RECORD" >/dev/null
}

# ---- uninstall ----------------------------------------------------------------------------------------
if [ $UNINSTALL = 1 ]; then
    bold "Removing the $TARGET install"
    if [ -f "$RECORD" ]; then
        while read -r f; do
            [ -n "$f" ] && { [ -e "$f" ] || [ -L "$f" ]; } && $SUDO rm -f "$f" && note "removed $f"
        done < "$RECORD"
        $SUDO rm -f "$RECORD"
    else
        note "nothing recorded for the $TARGET target ($RECORD)"
    fi
    if [ "$TARGET" = python ]; then
        "$PY" -m pip uninstall -y kicad2ems && note "kicad2ems uninstalled from $PY's user site"
    elif [ -d "$VENV" ] && ask "remove the venv $VENV too?" y; then
        $SUDO rm -rf "$VENV" && note "removed $VENV"
    fi
    command -v update-desktop-database >/dev/null && $SUDO update-desktop-database -q "$APPS" 2>/dev/null || true
    exit 0
fi

# ---- Python and the virtual environment -------------------------------------------------------------
bold "Python"
command -v "$PY" >/dev/null || { echo "  $PY not found (--python to name another)"; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || {
    echo "  kicad2ems needs Python 3.10 or newer; $PY is $("$PY" -V 2>&1)"; exit 1; }
PYVER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
note "$("$PY" -V) ($(command -v "$PY"))"
if [ "$TARGET" = python ] && "$PY" -c 'import sys, sysconfig, os; sys.exit(not os.path.exists(os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED")))'; then
    echo "  $PY is managed by your distribution (EXTERNALLY-MANAGED): pip may not install into it."
    echo "  Choose --target user instead (a venv of its own, the commands on your PATH)."
    exit 1
fi

# openEMS first: one this Python has is only seen from a venv with --system-site-packages; one in another venv is
# linked in (its CSXCAD and openEMS packages), which needs the same Python version
bold "openEMS (3D FDTD; not on PyPI)"
SYS_OEMS=0 EXT_PY=
"$PY" -c 'import CSXCAD, openEMS' 2>/dev/null && SYS_OEMS=1
FOUND=
for c in /opt/openems/venv/bin/python "$HOME/opt/openEMS/venv/bin/python" "$HOME/venvs/openems/bin/python" \
         "$REPO/.venv/bin/python"; do
    [ -x "$c" ] && [ "$(realpath "$c")" != "$(realpath "$(command -v "$PY")")" ] && \
        "$c" -c 'import CSXCAD, openEMS' 2>/dev/null && { FOUND=$c; break; }
done
case $OEMS_ARG in
    '') opts=()
        [ $SYS_OEMS = 1 ] && opts+=("this     $PY has it: $("$PY" -c 'import openEMS, os; print(os.path.dirname(openEMS.__file__))')")
        opts+=("elsewhere  another venv's openEMS, linked in${FOUND:+ (found: $FOUND)}")
        opts+=("build    a copy built with tools/build_openems.sh into $OEMS_PREFIX (10-20 min)")
        opts+=("none     mom25d and the line model only")
        def=${#opts[@]}
        [ $SYS_OEMS = 1 ] && def=1
        [ $SYS_OEMS = 0 ] && [ -n "$FOUND" ] && def=1
        choose "openEMS for kicad2ems:" $def "${opts[@]}"
        OEMS=${CHOICE%% *} ;;
    build|none) OEMS=$OEMS_ARG ;;
    *) OEMS=elsewhere; EXT_PY=$OEMS_ARG ;;
esac
if [ "$OEMS" = elsewhere ]; then
    if [ -z "$EXT_PY" ]; then
        askpath "the Python of the venv openEMS is installed in:" "$FOUND"
        EXT_PY=$ANSWER
    fi
    [ -d "$EXT_PY" ] && EXT_PY=$EXT_PY/bin/python                  # a venv's folder
    if [ -z "$EXT_PY" ] || ! "$EXT_PY" -c 'import CSXCAD, openEMS' 2>/dev/null; then
        note "no openEMS in ${EXT_PY:-(none given)} (import CSXCAD, openEMS failed): going on without"
        OEMS=none
    else
        EXTVER=$("$EXT_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        if [ "$EXTVER" != "$PYVER" ]; then
            note "that openEMS is built for Python $EXTVER, kicad2ems would run on $PYVER: its compiled modules"
            note "can't be shared. Run setup.sh again with --python python$EXTVER, or build openEMS for $PYVER."
            OEMS=none
        else
            note "linking the openEMS of $EXT_PY ($("$EXT_PY" -c 'import openEMS, os; print(os.path.dirname(openEMS.__file__))'))"
        fi
    fi
fi
[ "$OEMS" = this ] && [ $SYS_OEMS = 0 ] && OEMS=none

if [ -n "$VENV" ]; then
    bold "Virtual environment: $VENV"
    SITE=()
    [ "$OEMS" = this ] && SITE=(--system-site-packages)
    if [ -x "$VENV/bin/python" ] && ask "it exists: make it anew (otherwise it is kept and added to)?" n; then
        $SUDO rm -rf "$VENV"
    fi
    if [ ! -x "$VENV/bin/python" ]; then
        $SUDO mkdir -p "$(dirname "$VENV")"
        $SUDO "$PY" -m venv ${SITE[@]+"${SITE[@]}"} "$VENV"
        note "made with $("$VENV/bin/python" -V)${SITE:+, seeing the system packages (openEMS)}"
    fi
    VPY=$VENV/bin/python
else
    VPY=$(command -v "$PY")
fi
pipi() { $SUDO "$VPY" -m pip install ${USERFLAG[@]+"${USERFLAG[@]}"} "$@"; }
[ -n "$VENV" ] && pipi -q --upgrade pip setuptools wheel

# ---- optional parts ---------------------------------------------------------------------------------
bold "Optional parts"
EXTRAS=()
ask "GUI (kicad2ems-gui: PySide6, pyqtgraph, PyOpenGL for the 3D field view)?" y && EXTRAS+=(gui)
ask "frequency-domain field dumps and far fields (h5py)?" y && EXTRAS+=(dumps)
ask "line model with scikit-rf (--fast skrf; qucsator is a separate program)?" y && EXTRAS+=(fast)
MOM=0 GFDTD=0
ask "mom25d (2.5D MoM: planar circuits and antennas, no openEMS needed; from github.com/lowprocess/mom25d)?" y && { MOM=1; EXTRAS+=(mom); }
ask "gfdtd (3D FDTD on the GPU or a CPU core, openEMS's model; from github.com/lowprocess/gfdtd)?" y && { GFDTD=1; EXTRAS+=(gfdtd); }
ask "dfsynth (filter synthesis: the GUI's Filter page; from github.com/lowprocess/dfsynth)?" y && EXTRAS+=(filter)
CORE=0
if [ $MOM = 0 ] && [ $GFDTD = 0 ]; then
    :
elif command -v g++ >/dev/null || command -v c++ >/dev/null; then
    ask "build the compiled cores (C++ / OpenMP: mom25d ~5x, gfdtd ~15x faster on the CPU)?" y && { CORE=1; EXTRAS+=(core); }
else
    note "no C++ compiler (g++): the compiled cores are skipped (NumPy kernels: mom25d ~5x, gfdtd ~15x slower)"
fi
ask "test tools (pytest)?" n && EXTRAS+=(test)
[ "$OEMS" != none ] && EXTRAS+=(openems)        # openEMS's solver plugin (kicad2ems-openems; it brings h5py)

# ---- the GPU: CuPy for mom25d's direct solver -------------------------------------------------------
bold "GPU (CuPy: mom25d's LU in complex64, gfdtd's engine)"
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
if [ -z "$CUPY" ] && [ -n "$VENV" ] && [ -x "$VPY" ] && "$VPY" -m pip show -q cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x >/dev/null 2>&1; then
    ask "the venv has a CuPy wheel already: remove it (mom25d on the CPU)?" n && DROP_CUPY=1
fi

# ---- qucsator: the line model's circuit solver, a separate program ---------------------------------------
bold "qucsator (the line model's circuit solver; scikit-rf stands in without it)"
QUCS=
case $QUCS_ARG in
    none) ;;
    '') QUCS=$(command -v qucsator_rf || command -v qucsator || true)
        if [ -n "$QUCS" ]; then
            note "found: $QUCS"
        else
            DEPS=
            command -v dnf >/dev/null && DEPS="sudo dnf install qucs-s"
            command -v apt-get >/dev/null && DEPS="sudo apt install qucs-s"
            opts=("path     give the path of a qucsator_rf / qucsator you have")
            [ -n "$DEPS" ] && opts+=("install  from your distribution: $DEPS")
            opts+=("none     scikit-rf solves the line model instead")
            choose "qucsator:" ${#opts[@]} "${opts[@]}"
            case ${CHOICE%% *} in
                path) askpath "qucsator's path:" ""; QUCS=$ANSWER ;;
                install) $DEPS && QUCS=$(command -v qucsator_rf || command -v qucsator || true) ;;
            esac
        fi ;;
    *) QUCS=$QUCS_ARG ;;
esac
if [ -n "$QUCS" ] && [ ! -x "$QUCS" ]; then
    note "$QUCS is not an executable: going on without qucsator"
    QUCS=
fi

# ---- install ----------------------------------------------------------------------------------------
SPEC=$REPO
[ ${#EXTRAS[@]} -gt 0 ] && SPEC="$REPO[$(IFS=,; echo "${EXTRAS[*]}")]"
bold "Installing kicad2ems${EXTRAS:+ [$(IFS=,; echo "${EXTRAS[*]}")]}$([ $EDITABLE = 1 ] && echo ' (editable: this checkout'"'"'s code is what runs)' || echo ' (a copy of this checkout)')"
# mom25d's mesher: PyPI has no triangle >= 20230923 for every Python (none for 3.14): build it from its repository
if [ $MOM = 1 ] && ! "$VPY" -c 'import triangle' 2>/dev/null && ! pipi -q "triangle>=20230923" 2>/dev/null; then
    note "no triangle wheel for $("$VPY" -V): building it from github.com/drufat/triangle (needs a C compiler)"
    pipi "triangle @ git+https://github.com/drufat/triangle.git@$TRIANGLE_COMMIT"
fi
if [ $EDITABLE = 1 ]; then
    # compat: the checkout's path in a .pth inside the environment, ahead of ~/.local on sys.path. The default
    # editable finder runs after the path search, so a kicad2ems installed in ~/.local (visible through
    # --system-site-packages) was imported instead of this checkout
    pipi -e "$SPEC" --config-settings editable_mode=compat
else
    pipi "$SPEC"
fi
WHERE=$(cd / && "$VPY" -c 'import kicad2ems, os; print(os.path.dirname(os.path.dirname(kicad2ems.__file__)))')
if [ $EDITABLE = 1 ] && [ "$WHERE" != "$REPO/src" ]; then
    note "WARNING: kicad2ems is imported from $WHERE, not this checkout: remove that copy"
    note "(e.g. $PY -m pip uninstall kicad2ems, outside the venv) and run ./setup.sh again"
fi
SITEDIR=$("$VPY" -c "$([ "$TARGET" = python ] && echo 'import site; print(site.getusersitepackages())' || echo 'import sysconfig; print(sysconfig.get_paths()["purelib"])')")
SITECFG=$("$VPY" -c "$([ "$TARGET" = python ] && echo 'import site, os; print(os.path.join(site.getuserbase(), "kicad2ems-site.toml"))' || echo 'import sys, os; print(os.path.join(sys.prefix, "kicad2ems-site.toml"))')")
if [ -n "$CUPY" ]; then
    bold "Installing $CUPY"
    # one CuPy wheel at a time: another one in the venv would shadow it
    for other in cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x; do
        [ "$other" != "$CUPY" ] && $SUDO "$VPY" -m pip uninstall -y -q "$other" 2>/dev/null || true
    done
    pipi "$CUPY>=14"
elif [ $DROP_CUPY = 1 ]; then
    $SUDO "$VPY" -m pip uninstall -y cupy-rocm-7-0 cupy-cuda12x cupy-cuda13x 2>/dev/null || true
fi
if [ $CORE = 1 ] && [ $MOM = 1 ]; then
    bold "Building mom25d's compiled core"
    (cd / && $SUDO "$VPY" -m mom25d.core.build) || note "the core did not build: mom25d runs on NumPy (see the error above)"
fi
if [ $CORE = 1 ] && [ $GFDTD = 1 ]; then
    bold "Building gfdtd's compiled core"
    (cd / && $SUDO "$VPY" -m gfdtd.core.build) || note "the core did not build: gfdtd runs on NumPy on the CPU (see the error above)"
fi

# ---- openEMS: linked in from another venv, or built from source ---------------------------------------
if [ "$OEMS" = elsewhere ]; then
    bold "Linking openEMS from $EXT_PY"
    LINK=$([ -n "$VENV" ] && echo "$VENV/kicad2ems-openems" || echo "$DATA/kicad2ems/openems-link")
    $SUDO mkdir -p "$LINK"
    for pkg in CSXCAD openEMS; do
        src=$("$EXT_PY" -c "import $pkg, os; print(os.path.dirname($pkg.__file__))")
        $SUDO ln -sfn "$src" "$LINK/$pkg"
    done
    $SUDO mkdir -p "$SITEDIR"
    echo "$LINK" | $SUDO tee "$SITEDIR/kicad2ems-openems.pth" >/dev/null
    record "$SITEDIR/kicad2ems-openems.pth"
    if (cd / && "$VPY" -c 'import CSXCAD, openEMS') 2>/dev/null; then
        note "openEMS imports in kicad2ems's Python ($LINK)"
    else
        note "WARNING: linked, but import CSXCAD, openEMS fails in $VPY (see: $VPY -c 'import openEMS')"
    fi
fi
if [ "$OEMS" = build ]; then
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
    pipi -q cython numpy h5py setuptools
    if ask "build openEMS now (sources in $OEMS_PREFIX/src)?" y; then
        BENV=(SRC="$OEMS_PREFIX/src")
        [ -n "$VENV" ] && BENV+=(VIRTUAL_ENV="$VENV" PATH="$VENV/bin:$PATH")
        if $SUDO env "${BENV[@]}" bash "$REPO/tools/build_openems.sh" "$OEMS_PREFIX"; then
            # the venv's activate script finds the libraries; the GUI and kicad2ems started from it too
            if [ -n "$VENV" ] && ! grep -q 'kicad2ems: openEMS libraries' "$VENV/bin/activate"; then
                printf '\n# kicad2ems: openEMS libraries (setup.sh)\nexport LD_LIBRARY_PATH="%s/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n' \
                    "$OEMS_PREFIX" | $SUDO tee -a "$VENV/bin/activate" >/dev/null
            fi
            OEMS=built
        else
            note "the openEMS build failed (see above): kicad2ems is installed without it; fix and run setup.sh again"
        fi
    fi
fi

# ---- where the external programs are: kicad2ems-site.toml (src/kicad2ems/sitecfg.py) ------------------------
if [ -n "$QUCS" ]; then
    { echo "# written by setup.sh: where kicad2ems finds its external programs (src/kicad2ems/sitecfg.py)"
      echo "qucsator = \"$QUCS\""; } | $SUDO tee "$SITECFG" >/dev/null
    note "qucsator: $QUCS (in $SITECFG)"
    record "$SITECFG"
fi

# ---- commands on the PATH, the launcher entry, a desktop icon -------------------------------------------
GUI_CMD=$([ -n "$VENV" ] && echo "$VENV/bin/kicad2ems-gui" || "$VPY" -c 'import site, os; print(os.path.join(site.getuserbase(), "bin", "kicad2ems-gui"))')
if [ -n "$BIN" ]; then
    bold "Commands in $BIN"
    $SUDO mkdir -p "$BIN"
    for cmd in kicad2ems kicad2ems-gui; do
        [ -x "$VENV/bin/$cmd" ] || continue
        $SUDO ln -sfn "$VENV/bin/$cmd" "$BIN/$cmd"                    # the venv's own script: no activation needed
        record "$BIN/$cmd"
        note "$BIN/$cmd -> $VENV/bin/$cmd"
    done
    case ":$PATH:" in *":$BIN:"*) ;; *) note "$BIN is not on your PATH: add it (export PATH=\"$BIN:\$PATH\")" ;; esac
fi
if [[ " ${EXTRAS[*]-} " == *" gui "* ]] && [ -x "$GUI_CMD" ]; then
    bold "The GUI in the application launcher"
    if ask "a launcher entry for kicad2ems-gui (runs $GUI_CMD)?" y; then
        $SUDO mkdir -p "$APPS" "$ICONS"
        $SUDO cp "$REPO/kicad2ems/gui/kicad2ems.svg" "$ICONS/kicad2ems.svg"
        record "$ICONS/kicad2ems.svg"
        ENTRY=$(cat <<EOF
[Desktop Entry]
Type=Application
Name=kicad2ems
GenericName=EM simulation of KiCad boards
Comment=S-parameters, fields and antennas of KiCad boards: openEMS, mom25d and the line model
Exec="$GUI_CMD" %f
Icon=$ICONS/kicad2ems.svg
Terminal=false
Categories=Science;Engineering;Electronics;
StartupWMClass=kicad2ems
EOF
)
        echo "$ENTRY" | $SUDO tee "$APPS/kicad2ems.desktop" >/dev/null
        record "$APPS/kicad2ems.desktop"
        command -v update-desktop-database >/dev/null && $SUDO update-desktop-database -q "$APPS" 2>/dev/null || true
        note "$APPS/kicad2ems.desktop"
        DESK=$(command -v xdg-user-dir >/dev/null && xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
        if [ -d "$DESK" ] && [ "$DESK" != "$HOME" ] && ask "an icon on the desktop too ($DESK)?" n; then
            echo "$ENTRY" > "$DESK/kicad2ems.desktop"
            chmod +x "$DESK/kicad2ems.desktop"
            command -v gio >/dev/null && gio set "$DESK/kicad2ems.desktop" metadata::trusted true 2>/dev/null || true
            record "$DESK/kicad2ems.desktop"
            note "$DESK/kicad2ems.desktop"
        fi
    fi
fi

# ---- what this install can run ----------------------------------------------------------------------
bold "Check"
[ $OEMS = built ] && export LD_LIBRARY_PATH="$OEMS_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
K2E=$([ -n "$VENV" ] && echo "$VENV/bin/kicad2ems" || "$VPY" -c 'import site, os; print(os.path.join(site.getuserbase(), "bin", "kicad2ems"))')
(cd / && ${VENV:+env PATH="$VENV/bin:$PATH" VIRTUAL_ENV="$VENV"} "$K2E" --check) || true
[ -n "$CUPY" ] && "$VPY" -c "from mom25d.gpu import describe; print('  ' + describe())" || true
echo
echo "Done ($TARGET install). Use it with:"
case $TARGET in
    checkout) echo "  source ${VENV/#$REPO\//}/bin/activate"
              echo "  kicad2ems examples/demo_transition.toml        # or: kicad2ems-gui" ;;
    user|system) echo "  kicad2ems examples/demo_transition.toml        # or: kicad2ems-gui (also in the launcher)" ;;
    python)   echo "  $K2E examples/demo_transition.toml" ;;
esac
echo "Remove it again with: ./setup.sh --uninstall --target $TARGET"
