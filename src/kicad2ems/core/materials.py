"""
Laminates by name (R-E4): what a KiCad stackup's `material` says, as εr and tan
δ at the frequency the maker quotes them at. A dielectric of the stackup takes
its εr and tan δ from the file; where the file gives none, from here by its
material's name; where neither, FR-4's, and a note says so. Its reference
frequency (`fref`, Hz) is this table's for a named laminate: the wideband
(Djordjevic-Sarkar) model of mom25d and the FDTD layer (`--diel wideband`) holds
the layer's values there instead of at one `--diel-fref` for every layer. A
generic material (FR4, polyimide) has none: `--diel-fref` stands for it.

    lookup('Rogers RO4350B')  # dict(name='RO4350B', er=3.66, tand=0.0037,
                              # fref=10e9, source=...)
"""
import re
import numpy as np

# (canonical name, er, tan d, the frequency they are quoted at, Hz, where they
# come from, other names)
LAMINATES = [
    ('FR4', 4.5, 0.02, None,
     "KiCad's FR4 (a generic glass-epoxy: no one frequency)", ('fr-4', 'fr4',
                                                               'glass epoxy')),
    ('370HR', 4.04, 0.021, 1e9,
     'Isola 370HR, 50 % resin (its datasheet: 1 GHz)', ('isola370hr', )),
    ('FR408HR', 3.68, 0.0092, 1e9, 'Isola FR408HR (1 GHz)', ('fr408hr',
                                                             'fr408')),
    ('IS410', 4.0, 0.016, 1e9, 'Isola IS410 (1 GHz)', ()),
    ('RO4350B', 3.66, 0.0037, 10e9, 'Rogers RO4350B, design Dk (10 GHz)',
     ('ro4350', 'r4350b')),
    ('RO4003C', 3.55, 0.0027, 10e9, 'Rogers RO4003C, design Dk (10 GHz)',
     ('ro4003', 'r4003c')),
    ('RO4450F', 3.52, 0.004, 10e9, 'Rogers RO4450F prepreg (10 GHz)', ()),
    ('RO3003', 3.0, 0.001, 10e9, 'Rogers RO3003 (10 GHz)', ()),
    ('RT5880', 2.2, 0.0009, 10e9, 'Rogers RT/duroid 5880 (10 GHz)',
     ('duroid5880', 'rtduroid5880')),
    ('MEGTRON6', 3.71, 0.002, 1e9, 'Panasonic Megtron 6 (1 GHz)',
     ('r5775', 'megtron 6')),
    ('POLYIMIDE', 3.5, 0.002, None, 'polyimide film (generic)', ('kapton',
                                                                 'pi')),
    ('PTFE', 2.1, 0.0002, 10e9, 'PTFE (10 GHz)', ('teflon', )),
]
DEFAULT = 'FR4'


def _key(name):
    s = re.sub(r'\b(rogers|isola|panasonic|laminate|prepreg|core)\b', '',
               str(name or '').lower())
    return re.sub(r'[^a-z0-9]', '', s)


_BY = {}
for _n, _er, _td, _f, _src, _al in LAMINATES:
    for _a in (_n,) + _al:
        _BY[_key(_a)] = (_n, _er, _td, _f, _src)


def lookup(name):
    """The library's entry for a material's name (KiCad's, any case and
    punctuation; a maker's name before it is ignored), or None."""
    k = _key(name)
    if not k:
        return None
    hit = _BY.get(k)
    # 'RO4350B LoPro', 'FR408HR 1080'...: the longest name it starts with
    if hit is None:
        best = max((a for a in _BY if k.startswith(a) and len(a) >= 4), key=len,
                   default=None)
        hit = _BY.get(best) if best else None
    if hit is None:
        return None
    n, er, td, f, src = hit
    return dict(name=n, er=er, tand=td, fref=f, source=src)


def er_of(it, key='er'):
    """A stackup item's er (key='tand': its loss tangent) as Board takes it:
    the file's, else its material's from the library, else FR-4's (a mask's
    er 3.3, tan d 0). For the readers of the raw stack (KiCadBoard's
    stack_items, where KiCad's "Not specified" is None: max() and sums of it
    had raised TypeError); Board's own says so in a note."""
    v = it.get(key)
    if v is not None:
        return float(v)
    if it.get('kind') == 'mask':
        return 3.3 if key == 'er' else 0.0
    lib = lookup(it.get('material')) or lookup(DEFAULT)
    return float(lib[key])


# the Djordjevic-Sarkar curve's corners, 10^M1 .. 10^M2 Hz (1 kHz .. 1 THz)
M1, M2 = 3.0, 12.0


def djordjevic_sarkar(er, tand, fref, f):
    """Complex relative permittivity (eps' - j eps'', e^(+j w t)) at f (Hz) of
    the wideband Debye model through er (1 - j tand) at fref: eps_inf + d_eps /
    (M2 - M1) log10((w2 + j w) / (w1 + j w)), w1,2 = 2 pi 10^(M1,2) (the FDTD
    layer's kicad2ems.fdtd.dielectric and mom25d.multilayer have the same)."""
    def k(x):
        w = 2 * np.pi * np.asarray(x, float)
        return (np.log10((2 * np.pi * 10**M2 + 1j * w) /
                         (2 * np.pi * 10**M1 + 1j * w)) / (M2 - M1))
    kr = k(fref)
    d_eps = -er * tand / kr.imag
    return er - d_eps * kr.real + d_eps * k(f)
