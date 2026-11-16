"""
Net-name matching and the differential-port leg container.
"""


def resolve_nets(wanted, available):
    """Match requested net names to board nets. Hierarchical KiCad nets ('/sheet/REF_SMA_N') also
    match their bare name ('REF_SMA_N'); matching is case-sensitive first, then case-insensitive."""
    available = {str(n) for n in available if n}
    out = set()
    for w in wanted:
        hits = ({w} & available) or {n for n in available if n.rsplit('/', 1)[-1] == w.lstrip('/')} \
            or {n for n in available if n.rsplit('/', 1)[-1].lower() == w.lstrip('/').lower()}
        if not hits:
            raise ValueError(f"net {w!r} not found on the board; nets with copper: "
                             + ", ".join(sorted(available, key=str.lower)))
        out |= hits
    return out


class DiffLegs(dict):
    """{'+': footprint, '-': footprint} of a differential port (footprints are dicts themselves)."""
