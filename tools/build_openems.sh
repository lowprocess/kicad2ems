#!/bin/bash
# Build openEMS + CSXCAD from source (no GUI) and install their Python packages.
#   tools/build_openems.sh [prefix]          default prefix: $HOME/opt/openEMS
# Build dependencies first:
#   Debian / Ubuntu: sudo apt install build-essential cmake git libhdf5-dev libvtk9-dev libcgal-dev \
#                      libtinyxml-dev libboost-all-dev cython3 python3-dev
#   Fedora:          sudo dnf install gcc-c++ cmake git hdf5-devel vtk-devel CGAL-devel tinyxml-devel \
#                      boost-devel python3-Cython python3-devel
set -euo pipefail
PREFIX=${1:-$HOME/opt/openEMS}
SRC=${SRC:-$HOME/src/openEMS-Project}
if [ -d "$SRC/.git" ]; then                     # an existing checkout: update it if it is on a branch
    git -C "$SRC" pull --ff-only && git -C "$SRC" submodule update --init --recursive \
        || echo "note: could not update $SRC (not on a branch?) - building it as it is"
elif [ -d "$SRC" ]; then
    echo "note: $SRC is not a git checkout - building it as it is"
else
    git clone --recursive https://github.com/thliebig/openEMS-Project.git "$SRC"
fi
# pip: into the active virtual environment, or the user site (distributions block system-wide pip)
PIP_ARGS=()
if [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "no virtual environment active: installing the Python packages with --user"
    echo "(better: python3 -m venv --system-site-packages ~/venvs/openems && . ~/venvs/openems/bin/activate)"
    PIP_ARGS=(--user)
fi
mkdir -p "$SRC/build" && cd "$SRC/build"
cmake .. -DBUILD_APPCSXCAD=NO -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"
cd "$SRC/CSXCAD/python"  && CSXCAD_INSTALL_PATH="$PREFIX" python3 -m pip install ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} .
cd "$SRC/openEMS/python" && OPENEMS_INSTALL_PATH="$PREFIX" CSXCAD_INSTALL_PATH="$PREFIX" python3 -m pip install ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} .
echo
echo "openEMS installed in $PREFIX. If 'import openEMS' cannot find its libraries, add:"
echo "  export LD_LIBRARY_PATH=$PREFIX/lib:\$LD_LIBRARY_PATH"
