#!/bin/bash
# Build Elmer (ElmerSolver, ElmerGrid) from source into a prefix of its own: serial with OpenMP, UMFPACK (bundled),
# no MPI, no GUI. That is all kicad2ems-fem's tools use (install.py offers it; kicad2ems-fem's README).
#   tools/build_elmer.sh [prefix]          default prefix: $HOME/opt/elmer
# Build dependencies first:
#   Fedora:          sudo dnf install gcc-c++ gcc-gfortran cmake git blas-devel lapack-devel
#   Debian / Ubuntu: sudo apt install build-essential gfortran cmake git libblas-dev liblapack-dev
#   macOS:           brew install gcc cmake openblas (gfortran comes with gcc)
set -euo pipefail
PREFIX=${1:-$HOME/opt/elmer}
TAG=${ELMER_TAG:-release-26.2.1}               # the release kicad2ems-fem's tools are run against
SRC=${SRC:-$PREFIX/src/elmerfem}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}
if [ -d "$SRC/.git" ]; then
    echo "note: building the checkout in $SRC as it is"
else
    git clone --depth 1 --branch "$TAG" https://github.com/ElmerCSC/elmerfem.git "$SRC"
fi
mkdir -p "$SRC/build" && cd "$SRC/build"
cmake .. -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DWITH_OpenMP=TRUE -DWITH_MPI=FALSE -DWITH_ELMERGUI=FALSE -DWITH_ELMERPOST=FALSE -DWITH_MATC=FALSE \
    -DWITH_LUA=FALSE -DWITH_Mumps=FALSE -DWITH_Hypre=FALSE -DWITH_Trilinos=FALSE -DWITH_UMFPACK=TRUE
make -j"$JOBS"
make install
"$PREFIX/bin/ElmerSolver" -v </dev/null | head -1 || true
echo
echo "Elmer installed in $PREFIX: kicad2ems-fem finds it with K2E_ELMER=$PREFIX/bin (install.py records it)"
