#!/bin/bash
# Runs every topology through kicad2ems (mom25d + line model) with one EM tuning round.
#   ./validate_all.sh [outdir] [solvers]      default: dfsynth_validation, mom,lines
out=${1:-dfsynth_validation}; solvers=${2:-mom,lines}
mkdir -p "$out"
run() { name=$1; shift; echo "=== $name: $*"
  dfsynth "$@" -o "$out/$name" --validate "$solvers" --em-tune 1 > "$out/$name.log" 2>&1
  grep -E "^  (model|mom25d|gfdtd|openems|scikit|qucs) |copper \(|tuning|too large|dfsynth:" "$out/$name.log"; }
run bpf_coupled    --type bandpass --topology coupled    --f0 5G   --bw 500M --order 5 --substrate RO4003C-0.813
run bpf_hairpin    --type bandpass --topology hairpin    --f0 2.4G --bw 300M --order 5 --substrate FR4-1.6
run bpf_endcoupled --type bandpass --topology endcoupled --f0 10G  --bw 300M --order 3 --substrate Alumina-0.635
run bpf_stub       --type bandpass --topology stub       --f0 3G   --bw 1.5G --order 5 --substrate RO4003C-0.508
run hpf_stub       --type highpass --topology stub       --f0 2G             --order 5 --substrate RO4003C-0.508
run lpf_stepped    --type lowpass  --topology stepped    --f0 2G             --order 5 --substrate RO4003C-0.508
run lpf_stub       --type lowpass  --topology stub       --f0 2G             --order 5 --substrate RO4003C-0.508
run bsf_stub       --type bandstop --topology stub       --f0 2.4G --bw 1G   --order 5 --substrate RO4003C-0.508
