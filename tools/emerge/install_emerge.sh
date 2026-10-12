#!/usr/bin/env bash
# EMerge (3D FEM, github.com/FennisRobert/EMerge) from git into a Python, patched for Python 3.14, NumPy 2.4 and
# gmsh 4.15, so it can share kicad2ems's venv (--solvers emerge then needs no --emerge-python):
#   tools/emerge/install_emerge.sh .venv/bin/python [build dir, default .emerge]
# EMerge 2.8.9 pins Python < 3.14 and gmsh < 4.15, and emsutil 0.8.3 (its utilities) NumPy < 2.3. The patches
# (next to this script) widen those pins, and make EMerge's mesher skip mesh-size fields on entities that no
# longer exist: gmsh 4.15 throws on them from inside mesh generation, which aborted the process (4.14 ignored
# them). Results match EMerge on Python 3.13 / gmsh 4.14 (docs/emerge.md).
set -euo pipefail

PY=${1:?usage: install_emerge.sh PYTHON [BUILD_DIR]}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD=${2:-$(cd "$HERE/../.." && pwd)/.emerge}
EMERGE_TAG=v2.8.9
EMSUTIL_TAG=v0.8.3

fetch() {   # fetch name url tag patch: a clean checkout of the tag with the patch applied
    local name=$1 url=$2 tag=$3 patch=$4 dir=$BUILD/$1
    if [ -d "$dir/.git" ]; then
        git -C "$dir" fetch -q --depth 1 origin tag "$tag"
        git -C "$dir" checkout -q -f "$tag"
        git -C "$dir" clean -q -fdx
    else
        git clone -q --depth 1 --branch "$tag" "$url" "$dir"
    fi
    git -C "$dir" apply "$patch"
    echo "  $name $tag, patched ($(basename "$patch"))"
}

mkdir -p "$BUILD"
fetch emsutil https://github.com/FennisRobert/emsutil.git $EMSUTIL_TAG "$HERE/emsutil-$EMSUTIL_TAG.patch"
fetch EMerge https://github.com/FennisRobert/EMerge.git $EMERGE_TAG "$HERE/emerge-$EMERGE_TAG.patch"
"$PY" -m pip install "$BUILD/emsutil" "$BUILD/EMerge"
"$PY" -c "import emerge, numpy, gmsh, sys; print(f'  EMerge {emerge.__version__} on Python {sys.version.split()[0]}, NumPy {numpy.__version__}, gmsh {gmsh.__version__}')"
