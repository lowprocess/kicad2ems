"""
Part values with SI prefixes, as KiCad's fields write them ('4p7', '10k',
'1.2nH', '0R5'). A leaf: it imports nothing from kicad2ems, and pcbqs may
import it (tests/pcbqs/test_standalone.py lists what it may).

    value('33n')         3.3e-08: the whole text is one value, else None
    value('33n 10%')     None
    parse_value('33n 10%')  3.3e-08: the first word ('100n 50V X7R'), as a
                         part's Value field has it
"""
import re

__all__ = ['SI', 'value', 'parse_value']

SI = {
    'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
    'm': 1e-3, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9, 'R': 1.0, 'r': 1.0,
    'E': 1.0, '': 1.0
}
_VALUE = re.compile(
    r'^(\d*)(?:[.,](\d+))?([fpnuµμmkKMGRrE]?)(\d*)\s*(F|H|Ω|ohms?|OHMS?|R)?$')


def value(text):
    """'0R001' 1e-3, '4k7' 4700, '1u8' 1.8e-6, '2.2uH' 2.2e-6, '100n' 1e-7,
    '10m' 0.01; None if the whole text is not one value."""
    m = _VALUE.match(str(text).strip())
    if not m:
        return None
    whole, frac, pre, frac2, _ = m.groups()
    if not (whole or frac or frac2) or (frac and frac2):
        return None
    return float(f"{whole or '0'}.{frac or frac2 or '0'}") * SI[pre]


def parse_value(text):
    """The value of a Value field's first word: '100n 50V X7R' 1e-7, '2.2 nH'
    2.2e-9, '1,5p' 1.5e-12, '47Ω' 47; None if it is not one."""
    t = re.sub(r'^([\d.,]+)\s+(?=[fpnuµμmkKMGRrFHΩo])', r'\1',
               str(text).strip())
    word = re.split(r'[\s/_]', t)[0].replace('Ω', '')
    return value(word) if word else None
