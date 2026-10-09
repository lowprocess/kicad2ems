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


def test_toml_paths_relative_to_the_file(tmp_path, monkeypatch):
    d = tmp_path / 'proj'
    d.mkdir()
    (d / 'b.kicad_pcb').write_text('')
    (d / 'b-top.pos').write_text('')
    cfg = d / 'b.toml'
    cfg.write_text('board = "b.kicad_pcb"\npos = "b-top.pos"\nout = "out"\n')
    monkeypatch.chdir(tmp_path)                                    # somewhere else
    ap = parser()
    a = ap.parse_args([])
    apply_toml(a, ap, str(cfg), [])
    assert a.board == str(d / 'b.kicad_pcb') and a.pos == str(d / 'b-top.pos') and a.out == str(d / 'out')
    assert a.config_notes == []
    a = ap.parse_args(['other.kicad_pcb'])                         # a board typed on the command line wins
    apply_toml(a, ap, str(cfg), ['other.kicad_pcb'])
    assert a.board == 'other.kicad_pcb'


def test_toml_root_relative_paths_still_work(tmp_path, monkeypatch):
    (tmp_path / 'examples').mkdir()
    (tmp_path / 'examples' / 'b-top.pos').write_text('')
    cfg = tmp_path / 'examples' / 'old.toml'
    cfg.write_text('pos = "examples/b-top.pos"\nout = "examples/out"\n')
    monkeypatch.chdir(tmp_path)                                    # the repository root
    ap = parser()
    a = ap.parse_args(['b.kicad_pcb'])
    apply_toml(a, ap, str(cfg), ['b.kicad_pcb'])
    assert a.pos == 'examples/b-top.pos' and a.out == 'examples/out' and len(a.config_notes) == 2


def test_toml_as_the_only_argument(demo_files, tmp_path):
    base, _ = demo_files
    cfg = tmp_path / 'demo.toml'
    cfg.write_text(f'board = "{base}.kicad_pcb"\npos = "{base}-top.pos"\nnets = ["RF", "GND"]\nground = "B.Cu"\n'
                   f'fmin = 1\nfmax = 8\nout = "o"\n')
    r = subprocess.run([sys.executable, '-m', 'kicad2openems', str(cfg), '--preview'], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / 'o' / 'demo_mesh.png').exists()
