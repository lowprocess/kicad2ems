"""Command line: TOML config handling and an end-to-end --preview run."""
import os, sys, subprocess, json
from kicad2openems.cli import parser
from kicad2openems.config import apply_toml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_toml_config(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('nets = ["RF", "GND"]\nfmax = 8.0\nno_mask = true\ndmax = 0.4\n[openems]\nair = 3.0\nexcite = ["P1"]\n')
    ap = parser()
    argv = ['b.kicad_pcb', '--fmax', '12']
    a = ap.parse_args(argv)
    ignored = apply_toml(a, ap, str(p), argv)
    assert a.nets == 'RF,GND' and a.air == 3.0 and a.excite == 'P1' and a.mask is False
    assert a.fmax == 12                                            # command line wins
    assert ignored == ['dmax']                                     # other tools' keys are reported, not fatal


def test_preview_end_to_end(demo_files, tmp_path):
    base, _ = demo_files
    out = tmp_path / 'out'
    r = subprocess.run([sys.executable, '-m', 'kicad2openems', base + '.kicad_pcb', '--pos', base + '-top.pos',
                        '--nets', 'RF,GND', '--ground', 'B.Cu', '--fmin', '1', '--fmax', '8', '--out', str(out),
                        '--preview'], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'mesh:' in r.stdout and 'zmin=PEC' in r.stdout
    assert (out / 'demo_mesh.png').stat().st_size > 10000


def test_check_reports_python_and_modules():
    from kicad2openems.simulation import check_openems
    ok, lines = check_openems()
    text = "\n".join(lines)
    assert sys.executable in text and 'numpy' in text
    if not ok:
        assert 'FAILED' in text and 'hints:' in text                # the real error and a hint are shown
