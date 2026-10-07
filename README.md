# mom25d — an open-source 2.5D Method-of-Moments solver for PCB structures

A planar (2.5D) full-wave MoM electromagnetic solver in pure Python (NumPy/SciPy), aimed at
signal-integrity and RF structures on multilayer boards: traces, stubs, couplers, and — in
progress — via transitions and CPWG. The long-term goal is a Momentum-style workflow:
feed in a layout with port and lumped-component annotations and get S-parameters out.

![Coupler dashboard](docs/coupler_dashboard.png)

## Status

| Stage | Scope | State |
|---|---|---|
| 1 | Single dielectric layer (microstrip), RWG/MPIE, edge ports, S-parameters | done |
| 2a | Arbitrary multilayer stack (open or PEC top), metal on any interface, cross-layer coupling, diagnostics dashboard | done |
| 2b | Vias (vertical currents), ground antipads, CPWG with via fences | next |
| 3 | Lumped components, conductor thickness and loss | planned |
| 4 | KiCad / Gerber import, port annotations, CLI | planned |
| 5 | Speed: compression (ACA), frequency interpolation | planned |

## How it works

- **Formulation:** mixed-potential integral equation (MPIE), Galerkin testing, RWG basis
  functions on triangles.
- **Green's functions:** layered-media kernels from a spectral-domain transmission-line
  network (TE and TM), Sommerfeld integrals on a deformed contour. The 1/ρ singular part is
  subtracted spectrally and integrated analytically; the smooth remainder is tabulated per
  frequency and integrated by Gauss quadrature (high order only for near pairs).
- **Mesh:** non-uniform tensor grid on Manhattan (rectangle) geometry, with cells graded
  toward strip edges.
- **Ports:** delta-gap sources on a feed line; S-parameters from travelling-wave fits at
  reference planes (S = −B·A⁻¹ over all port excitations), Touchstone output.

## Files

| File | Contents |
|---|---|
| `mom25d.py` | Stage 1: single-layer solver, mesh, RWG, singular integrals, port extraction |
| `mom25d_ml.py` | Stage 2: `LayerStack`, multilayer Green's functions, `MultiLayerMoM`, `line_char_ml` |
| `mom25d_viz.py` | Plots: stackup, 3D view, mesh & ports, current density, S-params, kernels, timing, `dashboard()` |
| `mom_microstrip.py` | Standalone 2D quasi-static MoM for microstrip Z0 (teaching example) |
| `demo_mom25d.py` | Stage-1 demo: line characterisation, open end, shunt stub → `stub.s2p` |
| `demo_ml.py` | Stage-2 demo: broadside-coupled stripline λ/4 coupler → `coupler.s4p` + dashboard |
| `tests/` | Fast validation suite against closed-form results |
| `results/`, `docs/` | Example outputs |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest tests -q        # ~10 s
python demo_ml.py                # ~1.5 min, writes coupler_dashboard.png and coupler.s4p
```

Minimal example — a 50 Ω-ish stripline:

```python
from mom25d_ml import Layer, LayerStack, line_char_ml, stripline_z0_cohn
st = LayerStack([Layer(0.2e-3, 4.3), Layer(0.2e-3, 4.3)], top='pec')
gamma, zc = line_char_ml(st, layer=1, w=0.15e-3, freqs=[5e9])
print(zc[0].real, stripline_z0_cohn(0.15e-3, 0.4e-3, 4.3))   # ~56.5 vs 56.2 ohm
```

## Validation

| Check | Result |
|---|---|
| Multilayer Green's function vs stage 1 and vs exact image solution (incl. between layers) | ~1e-5 |
| Stripline Z0 vs Cohn's exact formula | within 0.6 % (6 graded cells across) |
| Stripline ε_eff | 4.2995 vs 4.3 exact, zero loss |
| Microstrip on a split substrate vs single layer | agree to 0.05 % on the same mesh |
| Microstrip open-end extension vs Hammerstad | 0.076 vs 0.077 mm |
| Broadside coupler (lossless, enclosed): power balance / reciprocity | 1 − 1e-9 / 5e-9 |
| Broadside coupler vs ideal coupled-line theory (solver-extracted Z0e/Z0o) | through & coupled within ~0.1 dB, 3–7 GHz |

## Limitations (current)

- Zero-thickness conductors; PEC unless a surface impedance is given (stage 1 only, untested).
- Rectangles only; no vias yet.
- Dense matrices: practical up to ~4000 unknowns per frequency point.
- The wave-fit port extraction needs feed lines of roughly λ/8 or longer.
