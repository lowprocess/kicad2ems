#!/usr/bin/env bash
# The boards of docs/segment-studies.md, at the commits the studies used, into
# a folder of their own (default ./boards). Public open-hardware projects:
#   Antmicro Baseboard for Jetson AGX Thor rev 1.1.0 (Apache-2.0)
#   LimeRFE 1v31 (Lime Microsystems / MyriadRF)
#   HackRF One and Opera Cake (Great Scott Gadgets, GPL-2.0)
set -euo pipefail
out=${1:-boards}
mkdir -p "$out"
get() {  # repo commit path file
  curl -sSfL -o "$out/$4" "https://raw.githubusercontent.com/$1/$2/$3"
  echo "$out/$4 ($(du -h "$out/$4" | cut -f1))"
}
THOR=507847a5f8063fcba7b4f78de358bedb68962145
LIME=412e190d36fcab2f0a5b8902dd3e4330019df56a
HACKRF=8540a765590bf67cade4139f6fc9513c238c7235
get antmicro/jetson-agx-thor-baseboard $THOR jetson-agx-thor-baseboard.kicad_pcb jetson-agx-thor-baseboard.kicad_pcb
get antmicro/jetson-agx-thor-baseboard $THOR jetson-agx-thor-baseboard.kicad_pro jetson-agx-thor-baseboard.kicad_pro
get myriadrf/LimeRFE $LIME hardware/1v31/KiCAD/Lime_RFE.kicad_pcb Lime_RFE_raw.kicad_pcb
get myriadrf/LimeRFE $LIME hardware/1v31/PDF/PCB_Manufacturing_Info-LimeRFE_1v31.pdf LimeRFE_fab.pdf
get greatscottgadgets/hackrf $HACKRF hardware/operacake/operacake.kicad_pcb operacake_raw.kicad_pcb
get greatscottgadgets/hackrf $HACKRF hardware/hackrf-one/hackrf-one.kicad_pcb hackrf-one.kicad_pcb
# the KiCad 4 boards carry no stackup: add the projects' documented ones
here=$(dirname "$0")
python "$here/add_stackup.py" limerfe "$out/Lime_RFE_raw.kicad_pcb" "$out/Lime_RFE.kicad_pcb"
python "$here/add_stackup.py" operacake "$out/operacake_raw.kicad_pcb" "$out/operacake.kicad_pcb"
