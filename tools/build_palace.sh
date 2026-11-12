#!/bin/bash
# Build Palace (github.com/awslabs/palace: S-parameters and capacitance by finite elements) for the CPU with Spack,
# into a prefix of its own: Spack itself, its build cache and its configuration all under it, nothing of ~/.spack.
# Palace with MPI, MFEM, hypre, libCEED and their dependencies: several hours and about 10 GB.
#   tools/build_palace.sh [prefix]         default prefix: $HOME/opt/palace-spack
# Needs a C, C++ and Fortran compiler, git, make, patch, bzip2, xz and a Python 3 (Spack's own requirements):
#   Fedora:          sudo dnf install gcc-c++ gcc-gfortran git make patch bzip2 xz python3 unzip
#   Debian / Ubuntu: sudo apt install build-essential gfortran git patch bzip2 xz-utils python3 unzip
# The newest Palace release: Spack's own package list lags (0.16.0 when this was written), so Palace's repository is
# checked out at its newest tag and its own Spack packages (spack_repo/local: Palace, and the MFEM, hypre, SuperLU
# and STRUMPACK it is tested with) are put first. PALACE_TAG=v0.18.1 builds another; PALACE_TAG=main the newest code.
# At the end $PREFIX/bin/palace is Palace's launcher, with its MPI beside it in $PREFIX/view/bin.
set -euo pipefail
PREFIX=${1:-$HOME/opt/palace-spack}
SPACK_TAG=${SPACK_TAG:-v1.2.2}
REPO_URL=https://github.com/awslabs/palace.git
if [ -z "${PALACE_TAG:-}" ]; then                   # the newest release's tag
    PALACE_TAG=$(git ls-remote --tags --refs "$REPO_URL" 'v*' | sed 's|.*refs/tags/||' | sort -V | tail -1)
fi
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}
mkdir -p "$PREFIX"
if [ ! -d "$PREFIX/spack/.git" ]; then
    git clone --depth 2 --branch "$SPACK_TAG" https://github.com/spack/spack.git "$PREFIX/spack"
fi
rm -rf "$PREFIX/palace-src"                         # Palace's own Spack packages, at that tag
git clone --depth 1 --branch "$PALACE_TAG" "$REPO_URL" "$PREFIX/palace-src"
# that tag's code with its package's recipe, as Palace's CI names it (a release's package.py may not list itself)
SPEC=${PALACE_SPEC:-local.palace@git.$PALACE_TAG=develop}
echo "Palace $PALACE_TAG: $SPEC"
export SPACK_DISABLE_LOCAL_CONFIG=1                 # this prefix's configuration alone: Spack's site scope
export SPACK_USER_CACHE_PATH="$PREFIX/cache"
. "$PREFIX/spack/share/spack/setup-env.sh"
spack compiler find >/dev/null || true
# as Palace's own CI builds it (.github/workflows/spack*.yml): its patches to Spack's packages, Spack's MFEM beside
# its Palace package (spack/spack#51505), its package repository first, and its spack.yaml's requirements
PACKAGES_COMMIT=$(grep -ho 'SPACK_PACKAGES_COMMIT: *[0-9a-f]\{40\}' "$PREFIX"/palace-src/.github/workflows/*.yml \
    | head -1 | grep -o '[0-9a-f]\{40\}' || true)
if [ -n "$PACKAGES_COMMIT" ]; then                  # Spack's packages at the commit Palace's CI builds with
    spack repo update --commit "$PACKAGES_COMMIT" builtin
fi
BUILTIN=$(spack location --repo builtin)
for patch in "$PREFIX"/palace-src/spack_repo/patches/*.diff; do
    [ -f "$patch" ] || continue
    if git -C "$BUILTIN" apply --check "$patch" 2>/dev/null; then
        git -C "$BUILTIN" apply "$patch" && echo "applied $(basename "$patch")"
    fi
done
[ -e "$PREFIX/palace-src/spack_repo/local/packages/mfem" ] || ln -s "$BUILTIN/packages/mfem" "$PREFIX/palace-src/spack_repo/local/packages/"
spack repo list | grep -q "$PREFIX/palace-src/spack_repo/local" || spack repo add "$PREFIX/palace-src/spack_repo/local"
cat > "$PREFIX/spack/etc/spack/packages.yaml" <<'YAML'
packages:
  c: {require: ["gcc"]}
  cxx: {require: ["gcc"]}
  fortran: {require: ["gcc"]}
  mpi: {require: openmpi}
  openmpi: {require: "@5:"}
  blas: {require: openblas}
  lapack: {require: openblas}
  petsc: {require: "~hdf5"}
  metis: {require: "cflags=-fPIC cppflags=-fPIC"}
  mfem: {require: "+exceptions"}
YAML
if [ "${SPEC_ONLY:-}" = 1 ]; then                 # SPEC_ONLY=1: what would be built, nothing built
    spack spec -I "$SPEC"
    exit 0
fi
spack install -j "$JOBS" --fail-fast "$SPEC"
rm -rf "$PREFIX/view"
spack view symlink -i "$PREFIX/view" "$SPEC"        # Palace and its MPI's mpirun side by side
mkdir -p "$PREFIX/bin"
ln -sf "$PREFIX/view/bin/palace" "$PREFIX/bin/palace"
echo
echo "Palace installed: $PREFIX/bin/palace. kicad2ems-fem finds it with K2E_PALACE=$PREFIX/bin/palace"
echo "(install.py records it)"
