# planemodes

Plane-pair cavity eigenmodes of printed circuit boards, read from **KiCad**
(`.kicad_pcb`, versions 6–10), **ODB++** (job folder, `.tgz`, `.tar` or `.zip`,
with net data) or **HyperLynx** (`.hyp`), in a native Qt viewer or from the
command line.

Between two ground planes a few tenths of a millimetre apart, the field is one
voltage V(x, y) between them. The planes' overlap resonates where it is not
tied down by ground vias; signal vias passing through it can drive those
resonances. planemodes finds them (frequency, Q, shape), shows them on the
board, animates them and says which vias they reach.

## Install

Everything comes from PyPI as wheels for CPython 3.10–3.14 on Linux (x86_64,
aarch64), Windows and macOS; the Triangle mesher comes compiled in meshpy:

```sh
pip install ./tools/planemodes             # or: pip wheel ./tools/planemodes
```

## Use

```sh
planemodes                                  # the viewer
planemodes board.kicad_pcb                  # the viewer with a board
planemodes run board.kicad_pcb --fmin 1 --fmax 10 --pairs F.Cu/In1.Cu --out results --images --contours 10
python -m planemodes ...                    # the same without the entry point
```

In the viewer:

1. **Open** a KiCad or HyperLynx file, or an ODB++ archive/folder. Ground nets are guessed
   from their names (GND, AGND, VSS…); edit the list if needed.
2. **Plane pairs** lists every pair of adjacent copper layers with its
   dielectric and ground coverage; the pairs with ground on both are ticked.
   Run one pair or many.
3. Set the **frequency** range, **mesh** (largest cell, or cells per
   wavelength; minimum triangle angle; refinement of narrow copper) and
   **solver** (mode limit; edge fringing
   and radiation on or off), then **Run**.
4. Pick a **mode** with the slider or the table. Colour by signed V (the
   nodes show), |V|, or the plane current; linear or log; each mode's own
   scale or a fixed one; the colour fill can be turned off. Overlay current
   arrows (on the chosen plane), **contour lines** of the coloured quantity,
   both or neither. Contours: lines every 1/N of the scale (N a decade on a
   log scale), width, dark / white / coloured by value, the nodal line
   (V = 0) in bold, negative V dashed; the colour bar marks the levels.
5. **Animate** plays V ∝ cos ωt with the current ∝ sin ωt.
   **Save image / frames / data** writes PNGs and `modes.csv`,
   `summary.json`, `fields.npz` (`planemodes.export.load_fields` reads it back).

From Python:

```python
from planemodes import load_board, StudyConfig, MeshConfig, run_study
board = load_board('board.kicad_pcb')
for r in run_study(board, StudyConfig(fmin=1e9, fmax=10e9, mesh=MeshConfig(min_angle=28))):
    print(r.cavity.name, r.modes.f / 1e9)
```

## Layout

| Module | Does |
|---|---|
| `model.py` | the board: stack-up, copper per layer and net (shapely), vias, outline |
| `readers/` | `kicad.py` (+ `sexpr.py`), `odbpp.py` (+ `lzw.py` for `.Z` files), `hyp.py`, shared `shapes.py` |
| `cavity.py` | a plane pair's overlap region, dielectric, ground vias (shorts) and other vias (ports) |
| `mesh.py` | Triangle meshes: wavelength sizing, quality angle, narrow-copper refinement |
| `solver.py` | finite elements, shift-invert eigen-solve in the frequency window, Q, via coupling |
| `edges.py` | the open edges: fringing field (microstrip model) and radiated power |
| `study.py` | configuration and the run over plane pairs |
| `export.py`, `raster.py` | files out; mode fields as images, contour lines and arrows (shared by the GUI) |
| `gui/` | Qt only: `app.py` (main window), `board_view.py`, `panels.py`, `worker.py` |

## Model and accuracy

- 2D parallel-plate cavity per plane pair: exact while the planes are close
  against the wavelength.
- **Edge fringing.** The field spills past every open copper edge, into air
  on an outer layer and into the dielectric on a buried one. It is added as
  a thin strip along each edge whose width and permittivity come from the
  Hammerstad–Jensen microstrip formulas (width 1.3 h, effective εr 2.2 for
  FR-4 on an outer layer). A 1 mm wide, 20 mm long outer-layer strip on
  0.2 mm FR-4 resonates within 0.5 % of the microstrip formula with it, and
  8.6 % low without it.
- **Radiation.** The edge voltages act as magnetic currents over a ground
  plane; their exact free-space coupling gives the radiated power and a
  Q_rad, combined with dielectric and conductor loss in Q. Outer pairs
  radiate from every open edge, buried ones only where they reach the board
  outline. A 20 × 30 mm patch on 1.6 mm FR-4 comes within 2 % of Balanis's
  two-slot Q_rad. The table shows Q and Q_rad.
- Ground vias are holes held at V = 0; holes under 2 mm² in a plane are
  filled; signal vias report the cavity's peak impedance at them.
- Accuracy is set by the copper's corners and slivers more than by the
  wavelength. Measured on the LibreVNA's F.Cu/In1.Cu against a converged
  reference (median frequency error): wavelength cells alone +5.9 %; with the
  default 25° minimum angle +1.9 % (13 s); 28° and copper under 2 mm refined
  to 0.15 mm +1.0 % (3 min). Frequencies come out high, and fall as the mesh
  is refined: check a result by raising the angle or refining.

## Readers

- **KiCad**: zones saved without fills are filled approximately (outline
  minus other nets' copper and the zone clearance): refill in KiCad for
  exact copper.
- **ODB++**: every standard symbol, including thermals with their spokes.
  NEGATIVE plane layers are the profile with their features clearing it;
  the plane's net is taken from `.eda_layers`, else from the vias that land
  on copper, else from the layer name. The stack-up comes from
  `matrix/stackup.xml` (ODB++ 8.1) when it names every copper layer, else
  from the matrix and layer attributes (including Altium's dielectric
  thickness on copper layers). Copper layers outside every drill span
  (unused layers some exporters write) are left out.
- **HyperLynx**: stack-up (DIELECTRIC `C=` is εr), outline, padstacks (metal,
  antipad, thermal rows), tracks, arcs, vias, pins, polygons with voids and
  pen width. PLANE-type polygons are cleared round other nets by the antipad
  or the plane separation; a PLANE layer with no polygons is flooded for the
  net whose holes connect to it. A pad no wider than its hole is an
  unplated hole. Files from KiCad's exporter are recognised and its quirks
  undone (pad angles, oval and rounded-rectangle shapes, KiCad 8's unflipped
  y, `EmptyNet` names): KiCad's ecc83 demo and an STM32 board read from their
  .hyp match the same boards read from .kicad_pcb to 0.02 and 0.3 mm².
- Every reader lists what it assumed in the log (and `board.notes`).
