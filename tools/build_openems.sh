#!/bin/bash
# Build openEMS + CSXCAD from source (no GUI) and install their Python packages.
#   tools/build_openems.sh [prefix]          default prefix: $HOME/opt/openEMS
# Build dependencies first:
#   Debian / Ubuntu: sudo apt install build-essential cmake git libhdf5-dev libvtk9-dev libcgal-dev \
#                      libtinyxml-dev libboost-all-dev cython3 python3-dev
#   Fedora:          sudo dnf install gcc-c++ cmake git hdf5-devel vtk-devel CGAL-devel tinyxml-devel \
#                      boost-devel python3-Cython python3-devel
set -e
PREFIX=${1:-$HOME/opt/openEMS}
SRC=${SRC:-$HOME/src/openEMS-Project}
[ -d "$SRC" ] || git clone --recursive https://github.com/thliebig/openEMS-Project.git "$SRC"
mkdir -p "$SRC/build" && cd "$SRC/build"
cmake .. -DBUILD_APPCSXCAD=NO -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"
cd "$SRC/CSXCAD/python"  && CSXCAD_INSTALL_PATH="$PREFIX" python3 -m pip install .
cd "$SRC/openEMS/python" && OPENEMS_INSTALL_PATH="$PREFIX" CSXCAD_INSTALL_PATH="$PREFIX" python3 -m pip install .
echo
echo "openEMS installed in $PREFIX. If 'import openEMS' cannot find its libraries, add:"
echo "  export LD_LIBRARY_PATH=$PREFIX/lib:\$LD_LIBRARY_PATH"
