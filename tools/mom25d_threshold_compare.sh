#!/usr/bin/env bash
# A config's mom25d sweep with the loop/tree basis's threshold at two values
# (MOM25D_LOOP_TREE_BELOW), and the largest |dS| between them. A sweep picks its
# own frequencies: a difference here is checked at fixed frequencies before it
# is believed (sim.sparams(f) both ways). The 2026-11-22 check (1e-5 against
# 1e-3) ran it on examples/demo_bandpass, demo_transition, hairpin,
# antenna_patch and demo_lowpass.
#   tools/mom25d_threshold_compare.sh examples/hairpin.toml [OLD [NEW]]
set -euo pipefail
cfg=$1; old=${2:-1e-5}; new=${3:-1e-3}; name=$(basename "$cfg" .toml)
py=${PYTHON:-python}
for thr in "$old" "$new"; do
  rm -rf "o_${name}_$thr"
  MOM25D_LOOP_TREE_BELOW=$thr $py -m kicad2ems.core.cli "$cfg" --solvers mom25d --no-report --lean \
    --out "o_${name}_$thr" > "${name}_$thr.log" 2>&1
  echo "$name $thr: $(grep -oE 'mom25d: [0-9]+ EM solves, [0-9.]+ s' "${name}_$thr.log" | tail -1)"
done
$py - "$name" "$old" "$new" <<'P'
import glob, sys, numpy as np
from kicad2ems.core.touchstone import read_touchstone
n, a, b = sys.argv[1:]
_, A = read_touchstone(glob.glob(f'o_{n}_{a}/*mom25d*.s*p')[0])
_, B = read_touchstone(glob.glob(f'o_{n}_{b}/*mom25d*.s*p')[0])
print(f'{n}: max |dS| {np.abs(A - B).max():.2e}')
P
