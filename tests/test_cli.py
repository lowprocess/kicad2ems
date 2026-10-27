"""Settings (defaults <- TOML <- command line), clean errors and end-to-end previews (in process)."""
import json, sys
import pytest
from kicad2ems.cli import settings_from_argv
from kicad2ems.config import build_settings, ConfigError, Settings


def test_toml_config(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('nets = ["RF", "GND"]\nfmax = 8.0\nno_mask = true\ndmax = 0.4\n[openems]\nair = 3.0\nexcite = ["P1"]\n')
    s, _ = settings_from_argv(['b.kicad_pcb', '--config', str(p), '--fmax', '12'])
    assert s.nets == ['RF', 'GND'] and s.air == 3.0 and s.excite == ['P1'] and s.mask is False
    assert s.fmax == 12                                            # command line wins
    assert any('dmax' in n for n in s.notes)                       # other tools' keys are reported, not fatal


def test_shortened_options_beat_the_toml(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nmax-steps = 3000\nexcite = "P2"\n')
    s, _ = settings_from_argv([str(p), '--max-st', '5000', '--exc', 'P1'])
    assert s.max_steps == 5000 and s.excite == ['P1']


def test_toml_lists_and_strings(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nbc = ["MUR", "MUR", "PEC", "PEC", "PEC", "MUR"]\nff_freq = [2.4, 2.5]\n'
                 'converge = "1,1.5"\nconverge_tol = [0.2, 3]\nregion = "0,0,10,5"\n')
    s, _ = settings_from_argv([str(p)])
    assert s.bc == 'MUR,MUR,PEC,PEC,PEC,MUR' and s.ff_freq == [2.4, 2.5]
    assert s.converge == [1.0, 1.5] and s.converge_tol == (0.2, 3.0) and s.region == (0, 0, 10, 5)


@pytest.mark.parametrize('values,match', [
    (dict(fmin=5, fmax=2), 'below fmax'), (dict(region='1,2,3'), 'region'), (dict(converge='1'), 'converge'),
    (dict(points='many'), 'points'), (dict(curves='round'), 'choose one of'), (dict(max_steps=2.5), 'whole number')])
def test_bad_settings_are_explained(values, match):
    with pytest.raises(ConfigError, match=match):
        build_settings(dict(board='b.kicad_pcb', **values))


def test_toml_paths_relative_to_the_file(tmp_path, monkeypatch):
    d = tmp_path / 'proj'
    d.mkdir()
    (d / 'b.kicad_pcb').write_text('')
    (d / 'b-top.pos').write_text('')
    cfg = d / 'b.toml'
    cfg.write_text('board = "b.kicad_pcb"\npos = "b-top.pos"\nout = "out"\n')
    monkeypatch.chdir(tmp_path)                                    # somewhere else
    s = build_settings({}, str(cfg))
    assert s.board == str(d / 'b.kicad_pcb') and s.pos == str(d / 'b-top.pos') and s.out == str(d / 'out')
    assert s.notes == []
    s = build_settings({'board': 'other.kicad_pcb'}, str(cfg))     # a board typed on the command line wins
    assert s.board == 'other.kicad_pcb'


def test_toml_root_relative_paths_still_work(tmp_path, monkeypatch):
    (tmp_path / 'examples').mkdir()
    (tmp_path / 'examples' / 'b-top.pos').write_text('')
    cfg = tmp_path / 'examples' / 'old.toml'
    cfg.write_text('pos = "examples/b-top.pos"\nout = "examples/out"\n')
    monkeypatch.chdir(tmp_path)                                    # the repository root
    s = build_settings({'board': 'b.kicad_pcb'}, str(cfg))
    assert s.pos == 'examples/b-top.pos' and s.out == 'examples/out' and len(s.notes) == 2


def test_check_is_command_line_only(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\ncheck = true\n')
    s = build_settings({}, str(p))
    assert any('check' in n for n in s.notes)                       # reported as not used, never silently


def test_check_reports_python_and_modules():
    from kicad2ems_openems.engine import check as check_openems
    ok, lines = check_openems()
    text = "\n".join(lines)
    assert sys.executable in text and 'numpy' in text
    if not ok:
        assert 'FAILED' in text and 'hints:' in text                # the real error and a hint are shown


def test_input_errors_are_messages_not_tracebacks(demo_files, run_cli, tmp_path):
    base, _ = demo_files
    code, out, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'FOO', '--out', tmp_path,
                             '--preview')
    assert code == 2 and 'kicad2ems: error:' in err and "net 'FOO' not found" in err and 'Traceback' not in err
    code, _, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'RF', '--out', tmp_path,
                           '--preview')
    assert code == 2 and 'no reference copper' in err and 'include the ground net in nets' in err
    code, _, err = run_cli(tmp_path / 'missing.kicad_pcb', '--preview')
    assert code == 2 and 'No such file' in err


def test_preview_report_and_summary(demo_files, run_cli, tmp_path):
    base, _ = demo_files
    out = tmp_path / 'out'
    code, so, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'RF,GND', '--ground', 'B.Cu',
                            '--fmin', '1', '--fmax', '8', '--out', out, '--preview', '--events', tmp_path / 'ev.jsonl')
    assert code == 0, err
    assert 'mesh:' in so and 'zmin=PEC' in so and 'transmission line P1 -> P2' in so
    assert (out / 'demo_mesh.png').stat().st_size > 10000 and (out / 'demo_tlines.png').exists()
    page = (out / 'demo_report.html').read_text()
    for part in ("id='summary'", "id='model'", "id='tlines'", "id='mesh'", 'data:image/png;base64,', 'P1',
                 'dielectric 1', 'microstrip', 'stripline'):
        assert part in page, part
    assert "id='openems'" not in page and "id='mom25d'" not in page   # no full-wave solve in a preview (auto: the line model)
    sm = json.loads((out / 'demo_run.json').read_text())
    assert sm['mode'] == 'preview' and sm['lines']['paths'][0]['port'] == 'P1'
    from kicad2ems.report import build                         # the report rebuilds from the JSON alone
    assert "id='tlines'" in build(sm, str(out))
    kinds = [json.loads(l)['kind'] for l in (tmp_path / 'ev.jsonl').read_text().splitlines()]
    assert 'stage' in kinds and 'log' in kinds and 'file' in kinds and kinds[-1] == 'done'
    code, _, _ = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'RF,GND', '--ground', 'B.Cu',
                         '--out', tmp_path / 'o2', '--preview', '--no-report')
    assert code == 0 and not (tmp_path / 'o2' / 'demo_report.html').exists()


def test_toml_as_the_only_argument(demo_files, run_cli, tmp_path):
    base, _ = demo_files
    cfg = tmp_path / 'demo.toml'
    cfg.write_text(f'board = "{base}.kicad_pcb"\npos = "{base}-top.pos"\nnets = ["RF", "GND"]\nground = "B.Cu"\n'
                   f'fmin = 1\nfmax = 8\nout = "o"\npreview = true\nno_report = true\n')
    code, _, err = run_cli(cfg)
    assert code == 0, err
    assert (tmp_path / 'o' / 'demo_mesh.png').exists()


def test_settings_defaults_match_the_dataclass():
    s, _ = settings_from_argv(['b.kicad_pcb'])
    assert s.options() == dict(Settings(board='b.kicad_pcb').options())


def test_events_in_a_new_output_folder(demo_files, run_cli, tmp_path):
    base, _ = demo_files
    out = tmp_path / 'fresh' / 'out'
    code, _, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'RF,GND', '--ground', 'B.Cu',
                           '--out', out, '--events', out / 'run.jsonl', '--preview', '--no-report')
    assert code == 0, err
    assert (out / 'run.jsonl').read_text().strip().endswith('"kind": "done", "code": 0}')


def test_empty_converge_means_off(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nconverge = []\n')
    assert build_settings({}, str(p)).converge is None


def test_config_typos_are_named(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nfmx = 6\nmom-dmx = 0.5\ndmax = 0.4\n[OpenEMS]\nend_db = -30\n')
    s = build_settings({}, str(p))
    note = next(n for n in s.notes if n.startswith('ignoring'))
    assert 'fmx (did you mean fmax?)' in note and 'mom_dmx (did you mean mom_dmax?)' in note
    assert 'dmax' in note and 'dmax (did you mean' not in note    # mom25d's own key: no wrong guess
    assert s.end_db == -30                                          # the [openems] table in any case


def test_init_writes_a_starter_config(demo_files, run_cli, tmp_path):
    import shutil
    base, _ = demo_files
    for ext in ('.kicad_pcb', '-top.pos'):
        shutil.copy(base + ext, tmp_path / ('b' + ext))
    code, so, err = run_cli('--init', tmp_path / 'b.kicad_pcb', '--ground', 'B.Cu', '--fmax', '8')
    assert code == 0 and '2 port(s) found' in so, so + err
    text = (tmp_path / 'b.toml').read_text()
    assert '#   P1: F.Cu' in text and 'pos     = "b-top.pos"' in text and '[solver.openems]' in text
    s = build_settings({}, str(tmp_path / 'b.toml'))
    assert s.notes == [] and s.fmax == 8 and s.ground == 'B.Cu' and s.nets == ['auto'] and s.pos == str(tmp_path / 'b-top.pos')
    code, _, err = run_cli('--init', tmp_path / 'b.kicad_pcb')       # never over a config that is there
    assert code == 2 and 'exists' in err
    text = open(base + '.kicad_pcb').read().replace('"P1"', '"X1"').replace('"P2"', '"X2"')
    (tmp_path / 'c.kicad_pcb').write_text(text)                      # no port footprints: none found, and why
    code, so, _ = run_cli('--init', tmp_path / 'c.kicad_pcb')
    assert code == 0 and 'no ports found' in so and 'Fab text P1' in (tmp_path / 'c.toml').read_text()
