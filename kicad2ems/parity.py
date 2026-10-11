"""
Parity: what a solver holds, read back from its native model, against the board - minus what it declared.
A solver may approximate the board, but it must say so: any difference it did not declare is a finding.

    devs = solver.deviations(job, prep)          # [solvers.Deviation], by the solver's own rules
    rb = solver.readback(job, prep)              # what its native model holds (None: no readback)
    problems = check(job.m, rb, devs)            # [] when every difference was declared

Compared: every via (position, radius, the heights it spans), edge walls, copper area per layer, the elements
of each lumped part, the ports. A declared deviation changes what is expected: a via moved to `to`, an item
`dropped`, vias `added` for it (edge walls as via rows), copper `area` added on a `layer`, a part made of `to`
elements.
"""

TOL = 1e-4                  # mm, positions and heights
ATOL = 1e-3                 # mm2, copper area (plus 1e-6 of it)


def _match(what, want, got, tol=TOL):
    """Items (lists of numbers, or a leading string and numbers) of want and got paired within tol: what is left."""
    out, left = [], [list(g) for g in got]
    for w in want:
        k = next((i for i, g in enumerate(left) if len(g) == len(w) and
                  all((a == b) if isinstance(a, str) else abs(a - b) <= tol for a, b in zip(g, w))), None)
        if k is None:
            out.append(f"{what}: the board's {_fmt(w)} is not in the solver's model")
        else:
            left.pop(k)
    out += [f"{what}: {_fmt(g)} in the solver's model is not on the board" for g in left]
    return out


def _fmt(v):
    return "[" + ", ".join(x if isinstance(x, str) else f"{x:.4f}" for x in v) + "]"


def check(m, rb, devs):
    """The undeclared differences between the board m and a solver's readback rb (see the module)."""
    if rb is None:
        return []
    by = {d.item: d for d in devs}
    out = []

    def expected(kind, items):
        want = []
        for i, v in enumerate(items):
            d = by.get(f"{kind}:{i}")
            if d is not None and d.dropped:
                continue
            want.append(list(d.to) if d is not None and d.to is not None else list(v))
        return want
    if 'vias' in rb:
        want = expected('via', m.vias) + [list(a) for d in devs for a in (d.added or [])]
        out += _match('via', want, rb['vias'])
    if 'walls' in rb:
        out += _match('edge wall', expected('wall', m.walls), rb['walls'])
    if 'copper' in rb:
        for lay in sorted(set(m.copper) | set(rb['copper'])):
            d = by.get(f"copper:{lay}")
            want = 0.0 if d is not None and d.dropped else sum(p.area for p in m.copper.get(lay, []))
            want += sum(x.area for x in devs if x.layer == lay and x.area is not None)
            got = rb['copper'].get(lay, 0.0)
            if abs(got - want) > ATOL + 1e-6 * want:
                out.append(f"copper on {lay}: {got:.4f} mm2 in the solver's model, {want:.4f} mm2 expected")
    if 'parts' in rb:
        for p in m.parts:
            d = by.get(f"part:{p.ref}")
            want = 0 if d is not None and d.dropped else (d.to if d is not None and d.to is not None else 1)
            if rb['parts'].get(p.ref, 0) != want:
                out.append(f"part {p.ref}: {rb['parts'].get(p.ref, 0)} element(s) in the solver's model, {want} expected")
        out += [f"part {r}: in the solver's model, not on the board" for r in rb['parts'] if r not in {p.ref for p in m.parts}]
    if 'ports' in rb and rb['ports'] != [p.name for p in m.ports]:
        out.append(f"ports: {rb['ports']} in the solver's model, {[p.name for p in m.ports]} on the board")
    return out


def describe(devs):
    """One line per kind of deviation: 'vias: moved onto the mesh (22, up to 0.064 mm)'."""
    groups = {}
    for d in devs:
        groups.setdefault((d.feature, d.what), []).append(d)
    lines = []
    for (feat, what), ds in groups.items():
        amt = [d.amount for d in ds if d.amount is not None]
        n = len(ds) if ds[0].item.split(':')[0] in ('via', 'wall', 'part') else None
        extra = ", ".join(([str(n)] if n else []) + ([f"up to {max(amt):.3g} mm"] if amt else []))
        lines.append(f"{feat}: {what}" + (f" ({extra})" if extra else ''))
    return lines
