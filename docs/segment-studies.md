# Whole-board studies

How the whole-board results were made, what each setting does and why it has the value it has, and how to make
each result again. The method itself (vias as ports, each signal layer over ideal planes, the pieces joined as one
network) is in [Signal integrity: a board in pieces](si.md); the code is `kicad2ems.core.segment`; the scripts named
here are in `tools/segment_studies/` (`fetch_boards.sh` gets every board below at the commit used).

## The boards

| Board | Source (commit) | Layers | Selection | Notes |
|---|---|---|---|---|
| Raspberry Pi CM5 carrier ("CM5 Minima") | the user's own board (not public) | 4 | 36 high-speed nets (18 pairs) | the first board in pieces |
| Antmicro Baseboard for Jetson AGX Thor, rev 1.1.0 | [antmicro/jetson-agx-thor-baseboard](https://github.com/antmicro/jetson-agx-thor-baseboard) (`507847a`), Apache-2.0 | 10 | every differential pair's legs: 288 nets, `select_nets.py pairs` | the scale target: 22k tracks, 3.3k vias |
| LimeRFE 1v31 | [myriadrf/LimeRFE](https://github.com/myriadrf/LimeRFE) (`412e190`), `hardware/1v31/KiCAD` | 6 | 470 RF nets, `select_nets.py rf-wide` | KiCad 4, no stackup: `add_stackup.py limerfe` |
| HackRF Opera Cake | [greatscottgadgets/hackrf](https://github.com/greatscottgadgets/hackrf) (`8540a76`), `hardware/operacake` | 4 | `Net-(C1..C12-Pad1/2)` | KiCad 4, no stackup: `add_stackup.py operacake`; plain microstrip, no top pour |

The KiCad 4 boards needed the importer to map their renamed copper layers (`T`, `GND`, `C1F`: by number to F.Cu,
In*n*.Cu, B.Cu), close their hand-drawn outlines (loose ends within 0.1 mm joined; the face the parts are on is the
board, not a panel's frame) and cut stale zone fills back from other nets' copper (a fill saved before a track was
routed). Their stackups come from the projects' documents (`add_stackup.py` says which and what was assumed).

## The settings, and why

| Setting | Default | Why (the measurement) |
|---|---|---|
| `--edges coarse` | fine | trace edge rings are 80 % of a long bus's unknowns: a CM5 piece 28,964 → 3,941, its through paths within 0.07 dB. Not with `--pour keep` (below) |
| `--via-solver pbm` | the layer solver | the physics-based via model (`viamodel.py`): about a second a via group; PCIe's clock against a cut-out solved whole: Sdd21 within 0.15 dB rms |
| `--thickness grow` | sides | no side unknowns (a fine mesh at 10 mm of pour: 51k unknowns, 7.4k of them sides) |
| stripline groups | 3 plane spacings | crosstalk falls as exp(−π s / b); microstrip's 20 heights chained a 54-net bus |
| `K2E_SEG_GROUP_MAX` | 24 nets | a bigger group is grouped closer, down to 2 spacings (stripline) or 10 heights (microstrip), and says so |
| stitching vias in stripline pieces | within 1 mm (`K2E_SEG_STITCH`) | without them the two ideal planes carry a parallel-plate mode: −40 to −46 dB coupling at any distance; with them −67 dB median beyond 1.5 mm |
| windows (`K2E_SEG_WINDOW`) | groups past 32 nets | a core of ≤ 6 nets and its neighbours within the group's spacing; the Thor's In7 bus 11,920 s → 1,103 s, through paths within 0.05 dB |
| a window's core ports driven alone (`K2E_SEG_EXCITE_CORE`) | on | mom25d `--mom-excite`: every port terminated, the core's driven; the columns equal the full solve's to 1e-7; the In7 windows 1,103 s → 796 s |
| series parts as joints | — | an AC-coupling capacitor or resistor between pieces as its own R, L, C two-port: a lane through its capacitor is one channel |
| `--pour keep` (RF) | off | a GCPW line's coplanar ground: without it LimeRFE's return loss was up to 10 dB off |
| `K2E_SEG_POUR_REACH` | 1.5 mm | the pour kept this far from the piece's copper; 3 mm: \|dS\| 0.006, 1.5 mm: 0.007 against the pour to 6 mm on a fine mesh |
| `K2E_SEG_POUR_APART` | 1.5 mm | with the pour kept, GCPW nets 1.5 mm and more apart coupled at −68.6 dB median (−48.5 at worst) |
| small pieces in parallel (`K2E_SEG_JOBS`) | cores / 4, at most 6 | pieces of ≤ 8 ports and < 30 mm² of signal copper on the CPU beside the big ones on the GPU; each `--lean` |

## The Thor carrier (10 layers, 288 nets)

```
tools/segment_studies/fetch_boards.sh boards
NETS=$(python tools/segment_studies/select_nets.py pairs boards/jetson-agx-thor-baseboard.kicad_pcb)
systemd-run --user --scope -p MemoryMax=24G -p MemorySwapMax=0 \
  python -m kicad2ems.core.segment boards/jetson-agx-thor-baseboard.kicad_pcb --nets "$NETS" \
  --edges coarse --via-solver pbm --thickness grow --dmax 0.5 --fmin 0.1 --fmax 10 --points 201 --out seg
```

**Result (2026-11-21, RX 7800 XT, 31 GB):** 59 min, 12.7 GB peak; 92 channels (pairs, each through its AC-coupling
capacitors), 368 ports, 95 layer pieces (27 of them windows) and 140 via models, every channel connected; median
loss −0.33 / −1.05 / −2.41 dB at 1 / 5 / 10 GHz, the worst crosstalk 0.83 % NEXT (DP3.TX2). Outputs: the
Touchstone file, `segment.json` (each channel's figures and curves), `<board>_overlay.html` (the channels coloured
by a chosen figure, opened from the disk), `<board>_couplings.png` (the worst-coupled pairs on the board) and
`<board>_coupling_profiles.png` (where along each victim it comes from: the local backward-coupling coefficient).
That run predates the core-port excitation, `--lean` and the parallel small pieces.

**What it took**, in order (each is in the CHANGELOG with its numbers):

1. the layer pieces kept only their two adjacent planes, as whole sheets (a split plane above a stripline had been
   meshed: 626k unknowns → 28k);
2. stripline grouping at 3 spacings, the group cap, then windows;
3. stitching vias in stripline pieces (the parallel-plate floor, found with `coupling_vs_gap.py`);
4. mom25d on the GPU: the double Z only where it fits beside kept factors (a spilled one ran past the driver's
   watchdog: `VK_ERROR_DEVICE_LOST`); big networks' figures capped; the join a frequency at a time as a sparse
   network (39 GB dense); `coupling.modal` as two products (its einsum never finished at 568 ports);
5. channels: a lane through its AC capacitor is one channel; its ends on two different parts; series parts as
   joints between pieces;
6. connectivity, found by `unrouted()` (a pre-solve check that each channel's ports are joined through cuts on its
   own nets): a via in a connector's pad kept; via circles' holes filled and touches not cut; cut ports clear of
   the circle; pad ports on the layer their track meets; each net cut by its own vias' circles only (a lane past
   the SoM connector's 28-via group had been chopped into stubs: −39 dB); a part's pad in a via's circle given to
   the via model.

**Checks behind it:**

- windows against whole groups: run once with `K2E_SEG_WINDOW=0`, once as is, then
  `compare_results.py whole/segment.json windows/segment.json`: insertion loss within 0.04 dB on 91 channels, each
  victim's worst crosstalk within 0.008 points;
- the line model as a stand-in for a big stripline piece (`--layer-solver lines`): its loss within 0.1–0.5 dB of
  mom25d, but it couples only traces within 5 heights: 244 of 248 lane-to-lane couplings above −50 dB missing.
  Set aside for crosstalk;
- the sweep with each entry's delay taken out (`afs_delay_replay.py` on the In7 piece's result): 72 solves without,
  the 80 cap unconverged with: the set-valued fit's poles are common to all entries. Set aside.

## LimeRFE (6 layers, RF front end, 470 nets)

```
NETS=$(python tools/segment_studies/select_nets.py rf-wide boards/Lime_RFE.kicad_pcb)
systemd-run --user --scope -p MemoryMax=24G -p MemorySwapMax=0 \
  python -m kicad2ems.core.segment boards/Lime_RFE.kicad_pcb --nets "$NETS" --pour keep --thickness grow \
  --fmin 0.1 --fmax 6 --points 201 --out lime
```

Its RF lines are 50 Ω GCPW (0.79 mm, 0.25 mm gaps) on 20 mil RO4350B over the GND layer (top) and the PWR layer
(bottom). The 470 nets chain through their series parts into 178 channels (a ladder filter's series L and C as
joints, its shunt capacitors to the kept pour in the mesh); 291 layer pieces and 25 via models.

**The pour check** (11 nets of the high-band receive section, 0.1–6 GHz; the reference: the pour to 6 mm, fine mesh
everywhere, `K2E_SEG_POUR_REACH=6 K2E_SEG_POUR_ZONE=off ... --pour keep`, 33,659 unknowns, 32 min):

| | unknowns | time | max \|dS\| | S11, median difference |
|---|---|---|---|---|
| pour dropped | 5,937 | 17 s | 0.137 | 2.0 dB (up to 10 dB) |
| pour kept, 3 mm, one group | 14,378 | 57 s | 0.006 | 0.05 dB |
| pour kept, 1.5 mm, grouped 1.5 mm apart | 13,600 in 6 pieces | 27 s | 0.007 | 0.2 dB |
| pour kept, coarse edges | 6,464 | 15 s | 0.080 | 2.3 dB |

The whole region solved as one ordinary run did not fit (a 157 GB matrix: every pour on every layer). On a GCPW
test line (`gcpw_pour_study.py`) the whole pour with the signal zone was the cheapest (3,929 unknowns, \|dS\| 0.004):
the pour's area costs little, its edges and vias do.

**Left out, by the run itself (said in its log):** channels whose port has no reference copper (two legs of a
bottom-side SAW duplexer over a void in the PWR layer), and channels whose copper overlaps another net's in the file
(the 30 MHz RX line over a relay's pad; 84 net pairs touching the RF nets overlap: a KiCad 4 file whose nets were
renamed out of step with its tracks); series parts not fitted ('NF', '0RNF' in their value: R90, R93, R374, R375, not assembled per the BOM)
leave their chains open, as on the board. Its parts' values are written `<value>_<package>_<part number>`
(`1000pF_0402_GRM1555C1H102FA01D`), its ferrite beads `FB_600R_...` (600 ohm); checked against
`hardware/1v31/BOM/Lime_RFE_1v31.xls`, every series part on its RF chains is modelled but two on supply nets
(a 100 uH and a 2.2 uH with no value in the file).

**Time:** two pieces where several paths meet (about 27k unknowns each: 140–200 mm² of wide RF copper on the fine
mesh) take most of it; the rest are small. Expect 1.5–2 hours on this machine with the loop/tree basis to 1e-5; to 1e-3 (since 2026-11-22) these
pieces solve their 1-2 GHz in under 20 s instead of 2-4 min: expect under an hour (not yet run whole).

## Making a result again

1. `tools/segment_studies/fetch_boards.sh boards` (the boards at the commits above, the stackups added).
2. The commands above; `K2E_SEG_*` as in the settings table to reproduce a variant.
3. The checks: `compare_results.py` between two runs, `coupling_vs_gap.py` on a piece, `gcpw_pour_study.py`,
   `afs_delay_replay.py`; `tools/mom25d_ldl_trial.py PIECE_board.json 0.3,1,2,3,6 --zone --lt-below 1e-3` (a
   symmetric factor as the preconditioner; a piece's board file is in its folder under the run's output); the terminated-port solve is a test (`tests/mom25d/test_kicad2ems.py`,
   `test_terminated_ports_drive_a_few`).

Timings are this machine's (RX 7800 XT, i7-13700K class CPU, 31 GB); the ROADMAP has what would make them shorter.
