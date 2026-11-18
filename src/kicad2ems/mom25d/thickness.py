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
    mixed  sides where copper faces copper within board.MIXED_GAP or is
           narrower than board.MIXED_WIDE, edge on the other edges

Not modelled, so resolved to another mode:
- sides with the whole stack meshed or planes 'mesh': edge (the plates do
  not couple to a via's vertical current: around a via through a meshed
  plane's antipad they misplace its return, rf_msvia's whole stack S11 -15
  dB at 5 GHz, sheets -34, gfdtd with solid copper -23; edge -22.8 dB at 8
  GHz as gfdtd's); with planes 'holes' or 'slots' (the plates do not couple
  to the holes' and openings' unknowns): off;
- mixed as sides with the whole stack meshed or planes other than 'pec';
  with wave ports: sides (its edge part, as edge's, not checked with them);
- edge with wave ports: off (their S moved 3-7 x what gfdtd's solid copper
  moves it: rf_diffvia's pairs 0.32 with edge, gfdtd 0.043; lumped ports
  0.065); sides too where WAVE_SIDES is off.
"""
MODES = ('off', 'grow', 'sides', 'edge', 'mixed')
DEFAULT = 'sides'
# wave ports with the sides (sim: the plates run on through the feed's cuts,
# its gap across them; the line characterisations with the sides). On the 7
# wave-port boards their thickness shift follows the lumped ports' (RMS
# 0.007-0.022 apart over the band, the lumped shift 0.03-0.12). Off, the copper
# as sheets with wave ports: with the folds' sheet halves in the sides' own
# rules (before sides.Sides.strip_part) the charge along a short feed was
# noisy below 2 GHz (demo_transition 0.22 apart, |S11| up to 1.8)
WAVE_SIDES = True


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
    if mode == 'mixed' and (whole or planes != 'pec'):
        mode = 'sides'
    if mode == 'sides' and (whole or planes != 'pec'):
        mode = 'off' if planes in ('holes', 'slots') else 'edge'
        why.append("no sides with "
                   + ('the whole stack meshed' if whole else
                      f"planes {planes!r}") + " (not modelled)")
    wave = [p.name for p in ports or ()
            if isinstance(p, (LinePort, DiffPort))]
    if wave and mode == 'mixed':
        why.append(f"sides on every edge with wave ports ({', '.join(wave)})")
        mode = 'sides'
    if wave and (mode == 'edge' or mode == 'sides' and not WAVE_SIDES):
        why.append(f"no {mode} with wave ports ({', '.join(wave)}; not "
                   "modelled), the copper as sheets: --mom-port lumped for "
                   "them")
        mode = 'off'
    return mode, (f"thickness {requested!r}: {'; '.join(why)}, thickness "
                  f"{mode!r}" if why else None)
