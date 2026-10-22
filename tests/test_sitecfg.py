"""Where the external programs are (kicad2ems.sitecfg): the user's file (the GUI's Solvers and hardware window)
wins over the environment's (setup.sh's kicad2ems-site.toml), and qucsator and openEMS's venv are taken from
them when nothing else names them."""
from kicad2ems import sitecfg


def test_site_files(tmp_path, monkeypatch):
    user, env = tmp_path / 'user.toml', tmp_path / 'env.toml'
    exe = tmp_path / 'qucsator_rf'
    exe.write_text('#!/bin/sh\n')
    exe.chmod(0o755)
    user.write_text(f'qucsator = "{exe}"\n')
    env.write_text('qucsator = "/nowhere/qucsator"\nopenems_venv = "/opt/openems/venv"\n')
    monkeypatch.setattr(sitecfg, 'paths', lambda: [str(user), str(env)])
    monkeypatch.setattr(sitecfg, '_cache', None)
    assert sitecfg.get('qucsator') == str(exe) and sitecfg.get('openems_venv') == '/opt/openems/venv'
    assert sitecfg.get('nothing') is None
    from kicad2ems.solvers.lines.fastsolve import find_qucsator
    assert find_qucsator() == str(exe)                              # the recorded one, before the PATH
    monkeypatch.setattr(sitecfg, '_cache', None)


def test_set_user(tmp_path, monkeypatch):
    """The GUI's setting into ~/.config/kicad2ems/site.toml: the other keys kept, '' removes it, read back first."""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setattr(sitecfg, '_cache', None)
    sitecfg.set_user('openems_venv', '/opt/o/venv')
    sitecfg.set_user('qucsator', '/usr/bin/qucsator_rf')
    assert sitecfg.user_file() == str(tmp_path / 'kicad2ems' / 'site.toml') and sitecfg.paths()[0] == sitecfg.user_file()
    assert sitecfg.get('qucsator') == '/usr/bin/qucsator_rf' and sitecfg.get('openems_venv') == '/opt/o/venv'
    sitecfg.set_user('qucsator', '')
    assert sitecfg.get('qucsator') is None and sitecfg.get('openems_venv') == '/opt/o/venv'
    monkeypatch.setattr(sitecfg, '_cache', None)
