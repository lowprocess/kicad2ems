"""Add a documented stackup to a KiCad 4 board that has none (kicad2ems needs
one: Board Setup > Physical Stackup). usage: add_stackup.py PRESET IN OUT

  limerfe    LimeRFE 1v31, from PCB_Manufacturing_Info-LimeRFE_1v31.pdf: 1.6 mm,
             6 layers; top and bottom 20 mil (0.508 mm) Rogers RO4350B (er 3.66)
             over the GND and PWR layers, FR-4 between ("prepreg thicknesses
             are not important as long as the total is 1.6 mm": split evenly,
             er 4.3 assumed); 0.5 oz copper + plating (35 um used). Its 50 ohm
             RF lines: GCPW, 0.79 mm track, 0.25 mm gaps.
  operacake  HackRF Opera Cake, from hardware/operacake/README: 4 layers,
             0.0646 in; 1 oz outer, 6.7 mil prepreg, 0.5 oz inner, 47 mil core
             (OSH Park's 4-layer: FR408, er 3.66, tan d 0.0125 assumed).
"""
import sys

MIL = 0.0254


def layers(preset):
    if preset == 'limerfe':
        fr = (1.6 - 2 * 0.508 - 6 * 0.035) / 3
        ro = '(material "RO4350B") (epsilon_r 3.66) (loss_tangent 0.0037)'
        f4 = '(material "FR4") (epsilon_r 4.3) (loss_tangent 0.02)'
        return [('F.Mask', 'Top Solder Mask', 0.02, '(epsilon_r 3.3)'),
                ('F.Cu', 'copper', 0.035, ''),
                ('dielectric 1', 'core', 0.508, ro),
                ('In1.Cu', 'copper', 0.035, ''),
                ('dielectric 2', 'prepreg', fr, f4),
                ('In2.Cu', 'copper', 0.035, ''),
                ('dielectric 3', 'core', fr, f4),
                ('In3.Cu', 'copper', 0.035, ''),
                ('dielectric 4', 'prepreg', fr, f4),
                ('In4.Cu', 'copper', 0.035, ''),
                ('dielectric 5', 'core', 0.508, ro),
                ('B.Cu', 'copper', 0.035, ''),
                ('B.Mask', 'Bottom Solder Mask', 0.02, '(epsilon_r 3.3)')]
    if preset == 'operacake':
        f = '(material "FR408") (epsilon_r 3.66) (loss_tangent 0.0125)'
        return [('F.Mask', 'Top Solder Mask', 0.0254, '(epsilon_r 3.3)'),
                ('F.Cu', 'copper', 1.4 * MIL, ''),
                ('dielectric 1', 'prepreg', 6.7 * MIL, f),
                ('In1.Cu', 'copper', 0.7 * MIL, ''),
                ('dielectric 2', 'core', 47 * MIL, f),
                ('In2.Cu', 'copper', 0.7 * MIL, ''),
                ('dielectric 3', 'prepreg', 6.7 * MIL, f),
                ('B.Cu', 'copper', 1.4 * MIL, ''),
                ('B.Mask', 'Bottom Solder Mask', 0.0254, '(epsilon_r 3.3)')]
    raise SystemExit(f"unknown preset {preset!r}: limerfe, operacake")


def main(preset, src, dst):
    s = open(src, encoding='utf-8').read()
    if '(stackup' in s:
        raise SystemExit(f"{src} has a stackup already")
    body = '\n'.join(f'      (layer "{n}" (type "{t}") (thickness {th:.4f}) {x})'
                     for n, t, th, x in layers(preset))
    i = s.index('(setup') + len('(setup')
    open(dst, 'w', encoding='utf-8').write(
        s[:i] + f'\n    (stackup\n{body}\n    )\n' + s[i:])
    print(f"{dst}: the {preset} stackup added")


if __name__ == '__main__':
    main(*sys.argv[1:4])
