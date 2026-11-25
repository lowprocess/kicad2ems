"""
Part values with SI prefixes, as KiCad's fields write them ('4p7', '10k',
'1.2nH', '0R5'). A leaf: it imports nothing from kicad2ems, and pcbqs may
import it (tests/pcbqs/test_standalone.py lists what it may).

    value('33n')         3.3e-08: the whole text is one value, else None
    value('33n 10%')     None
    parse_value('33n 10%')  3.3e-08: the first word ('100n 50V X7R'), as a
                         part's Value field has it
    part_value('R_0603_10k', 'R')  1e4: a library's or a BOM's spelling, its
                         package code passed over
"""
import re

__all__ = ['SI', 'value', 'parse_value', 'part_value', 'PACKAGES']

SI = {
    'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
    'm': 1e-3, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9, 'R': 1.0, 'r': 1.0,
    'E': 1.0, '': 1.0
}
# the prefix's case matters ('m' milli, 'M' mega; SPICE's 'Meg' any case), the
# unit's does not ('10 kOhm', '5mOHM', '2.2 nH'); a space may stand before
# either ('100 nF', '0.1 uF')
_VALUE = re.compile(r'^(\d*)(?:[.,](\d+))?\s*((?i:meg)|[fpnuµμmkKMGRrE]?)(\d*)'
                    r'\s*((?i:ohms?|F|H|R)|Ω)?$')
# package codes ('R_0603_10k', 'C_0402_100nF'), imperial and metric: a
# library's value names its part by them, and they are not values
PACKAGES = frozenset(
    '01005 0201 0402 0603 0805 1008 1206 1210 1806 1812 2010 2220 2512 '
    '0402M 0603M 1005 1608 2012 3216 3225 4532 5025 6332'.split())


def value(text):
    """'0R001' 1e-3, '4k7' 4700, '1u8' 1.8e-6, '2.2uH' 2.2e-6, '100n' 1e-7,
    '10m' 0.01; None if the whole text is not one value."""
    m = _VALUE.match(str(text).strip())
    if not m:
        return None
    whole, frac, pre, frac2, _ = m.groups()
    if not (whole or frac or frac2) or (frac and frac2):
        return None
    scale = 1e6 if pre.lower() == 'meg' else SI[pre]
    return float(f"{whole or '0'}.{frac or frac2 or '0'}") * scale


def parse_value(text):
    """The value of a Value field's first word: '100n 50V X7R' 1e-7, '2.2 nH'
    2.2e-9, '1,5p' 1.5e-12, '47Ω' 47; None if it is not one."""
    t = re.sub(r'^([\d.,]+)\s+(?=[fpnuµμmkKMGRrFHΩoO])', r'\1',
               str(text).strip())
    word = re.split(r'[\s/_]', t)[0].replace('Ω', '')
    return value(word) if word else None


def part_value(text, kind=None):
    """The value of a part's Value field as libraries and BOMs write it: its
    first word ('100n 50V X7R', '10 kOhm', '1000pF_0402_GRM1555C1H102'), else
    the first of its words that is a value and not a package code
    ('R_0603_10k' 1e4, 'C_0402_100nF' 1e-7, 'C_22u_16V_0603' 2.2e-5). kind
    ('R', 'L' or 'C'): those words only after that letter ('C_10k' for a
    resistor is no value). None when there is none."""
    v = parse_value(text)
    if v is not None:
        return v
    words = [w for w in re.split(r'[\s/_]+', str(text).strip()) if w]
    if kind is not None:
        if len(words) < 2 or words[0].upper() != kind.upper():
            return None
        words = words[1:]
    for w in words:
        if w.upper() not in PACKAGES:
            v = value(w)
            if v is not None:
                return v
    return None
