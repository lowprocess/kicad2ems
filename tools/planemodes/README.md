# planemodes

Plane-pair cavity eigenmodes of printed circuit boards, read from **KiCad**
(`.kicad_pcb`, versions 6–10) or **ODB++** (job folder, `.tgz`, `.tar` or `.zip`,
with net data), in a native Qt viewer or from the command line.

Between two ground planes a few tenths of a millimetre apart, the field is one
voltage V(x, y) between them. The planes' overlap resonates where it is not
tied down by ground vias; signal vias passing through it can drive those
resonances. planemodes finds them (frequency, Q, shape), shows them on the
board, animates them and says which vias they reach.

## Install

Everything comes from PyPI (Triangle and Qt as compiled wheels):

```sh
pip install ./tools/planemodes             # or: pip wheel ./tools/planemodes
pip install pypardiso                      # optional: a multithreaded sparse solver
```

## Use

```sh
planemodes                                  # the viewer
planemodes board.kicad_pcb                  # the viewer with a board
planemodes run board.kicad_pcb --fmin 1 --fmax 10 --pairs F.Cu/In1.Cu --out results --images
python -m planemodes ...                    # the same without the entry point
```

In the viewer:

1. **Open** a KiCad file or an ODB++ archive/folder. Ground nets are guessed
   from their names (GND, AGND, VSS…); edit the list if needed.
2. **Plane pairs** lists every pair of adjacent copper layers with its
   dielectric and ground coverage; the pairs with ground on both are ticked.
   Run one pair or many.
3. Set the **frequency** range, **mesh** (largest cell, or cells per
   wavelength; minimum triangle angle; refinement of narrow copper) and
   **solver** (SuperLU, or PARDISO if installed; mode limit), then **Run**.
4. Pick a **mode** with the slider or the table. Colour by signed V (the
   nodes show), |V|, or the plane current; linear or log; each mode's own
   scale or a fixed one. Arrows show the current on the chosen plane.
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
| `readers/` | `kicad.py` (+ `sexpr.py`), `odbpp.py` (+ `lzw.py` for `.Z` files), shared `shapes.py` |
| `cavity.py` | a plane pair's overlap region, dielectric, ground vias (shorts) and other vias (ports) |
| `mesh.py` | Triangle meshes: wavelength sizing, quality angle, narrow-copper refinement |
| `solver.py` | finite elements, shift-invert eigen-solve in the frequency window, Q, via coupling |
| `study.py` | configuration and the run over plane pairs |
| `export.py`, `raster.py` | files out; mode fields as images and arrows (shared by the GUI) |
| `gui/` | Qt only: `app.py` (main window), `board_view.py`, `panels.py`, `worker.py` |

## Model and accuracy

- 2D parallel-plate cavity per plane pair: exact while the planes are close
  against the wavelength; edges open (no radiation, so Q is an upper bound).
- Ground vias are holes held at V = 0; holes under 2 mm² in a plane are
  filled; signal vias report the cavity's peak impedance at them.
- Accuracy is set by the copper's corners and slivers more than by the
  wavelength. Measured on the LibreVNA's F.Cu/In1.Cu against a converged
  reference (median frequency error): wavelength cells alone +5.9 %; with the
  default 25° minimum angle +1.9 % (13 s); 28° and copper under 2 mm refined
  to 0.15 mm +1.0 % (3 min). Frequencies come out high, and fall as the mesh
  is refined: check a result by raising the angle or refining.
- Outer-layer pours (microstrip-like) see part of their field in air; their
  modes sit higher than this model gives, by up to ~10 % for narrow slivers.
- KiCad zones saved without fills are filled approximately (outline minus
  other nets' copper and the zone clearance): refill in KiCad for exact copper.
  ODB++ thermal-relief symbols are read as plain rings.
