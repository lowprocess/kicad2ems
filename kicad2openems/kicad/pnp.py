"""
Pick-and-place files (.pos ASCII or .csv) and port-component detection.
"""
import re


def read_pos(path):
    """KiCad pick-and-place file (.pos ASCII or .csv). Returns list of dict(ref, val, package, x, y, rot, side)."""
    rows = []
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    if path.lower().endswith('.csv'):
        import csv
        for r in csv.DictReader(text.splitlines()):
            rows.append(dict(ref=r['Ref'], val=r['Val'], package=r['Package'], x=float(r['PosX']),
                             y=float(r['PosY']), rot=float(r['Rot']), side=r['Side']))
    else:
        for line in text.splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            p = line.split()
            if len(p) >= 7:
                rows.append(dict(ref=p[0], val=p[1], package=p[2], x=float(p[3]), y=float(p[4]),
                                 rot=float(p[5]), side=p[6]))
    return rows


def pos_port_refs(rows, pattern=r'^P\d+[+\-PN]?$', package_hint='PORT'):
    """References of port components: Value or Reference P<n>, or package containing the hint."""
    pat = re.compile(pattern, re.I)
    return {r['ref'] for r in rows if pat.match(r['val']) or pat.match(r['ref'])
            or (package_hint and package_hint.lower() in r['package'].lower())}
