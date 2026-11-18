"""
mom25d.thickness - how the copper's thickness is modelled, decided in one
place: resolve(requested, model, ports) -> (mode, reason). BoardModel builds
what the mode needs and PlanarModel.simulation hands it to the Simulation;
the reason is the run's note.

    off    the copper as zero-thickness sheets
    grow   each sheet's edges moved out by board.GROW_T x its copper's
           thickness (the effective width)
    sides  every copper edge stood up as a plate the copper's thickness high
           (mom25d.sides; the default)
    edge   the edge triangles' near terms averaged over the copper's height
           (mom25d.edge), no unknowns added

Not modelled, so resolved to another mode:
- sides with the whole stack meshed or planes 'mesh': edge (the plates do
  not couple to a via's vertical current: around a via through a meshed
  plane's antipad they misplace its return, rf_msvia's whole stack S11 -15
  dB at 5 GHz, sheets -34, gfdtd with solid copper -23; edge -22.8 dB at 8
  GHz as gfdtd's); with planes 'holes' or 'slots' (the plates do not couple
  to the holes' and openings' unknowns): off;
- sides or edge with wave ports: off (their S moved 3-7 x what gfdtd's solid
  copper moves it: rf_diffvia's pairs 0.24 with sides, 0.32 with edge, gfdtd
  0.043; lumped ports 0.046 and 0.065), unless WAVE_SIDES.
"""
MODES = ('off', 'grow', 'sides', 'edge')
DEFAULT = 'sides'
# wave ports with the sides (sim: the plates run on through the feed's cuts,
# its gap across them; the line characterisations with the sides): off, the
# copper as sheets with wave ports. Above 2 GHz they follow the lumped ports'
# shift on the 7 wave-port boards (RMS 0.004-0.03 apart); below it on short
# feeds the sides' charge along a feed is noisy at some cell shapes
# (demo_transition's P1 at 1 GHz: the cuts' current +-4 %, 0.08 % as sheets),
# and the fit amplifies it (|S11| up to 1.8; rf_diffvia's pair 0.17 off)
WAVE_SIDES = False


def resolve(requested, model, ports=None):
    """(mode, reason): the thickness model for `requested` (one of MODES) on
    the board model (its planes, whether the whole stack is meshed) with the
    simulation's ports (None: the board's rules alone), and why it is not the
    one requested (None where it is)."""
    from .geo import LinePort
    from .sim import DiffPort
    if requested not in MODES:
        raise ValueError(
            f"thickness {requested!r}: {', '.join(MODES[:-1])} or {MODES[-1]}")
    planes = getattr(model, 'planes', 'pec')
    whole = getattr(model, 'whole', False)
    mode, why = requested, []
    if mode == 'sides' and (whole or planes != 'pec'):
        mode = 'off' if planes in ('holes', 'slots') else 'edge'
        why.append("no sides with "
                   + ('the whole stack meshed' if whole else
                      f"planes {planes!r}") + " (not modelled)")
    wave = [p.name for p in ports or ()
            if isinstance(p, (LinePort, DiffPort))]
    if mode in ('sides', 'edge') and wave and not WAVE_SIDES:
        why.append(f"no {mode} with wave ports ({', '.join(wave)}; not "
                   "modelled), the copper as sheets: --mom-port lumped for "
                   "them")
        mode = 'off'
    return mode, (f"thickness {requested!r}: {'; '.join(why)}, thickness "
                  f"{mode!r}" if why else None)
