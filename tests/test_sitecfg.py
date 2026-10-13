"""Where setup.sh recorded the external programs (kicad2ems-site.toml, kicad2ems.sitecfg): the environment's file
wins over the user's, and qucsator and EMerge's Python are taken from it when nothing else names them."""
import os
from kicad2ems import sitecfg


def test_site_files(tmp_path, monkeypatch):
    env, user = tmp_path / 'env.toml', tmp_path / 'user.toml'
    exe = tmp_path / 'qucsator_rf'
    exe.write_text('#!/bin/sh\n')
    exe.chmod(0o755)
    env.write_text(f'qucsator = "{exe}"\n')
    user.write_text('qucsator = "/nowhere/qucsator"\nemerge_python = "/opt/emerge/bin/python"\n')
    monkeypatch.setattr(sitecfg, 'paths', lambda: [str(env), str(user)])
    monkeypatch.setattr(sitecfg, '_cache', None)
    assert sitecfg.get('qucsator') == str(exe) and sitecfg.get('emerge_python') == '/opt/emerge/bin/python'
    assert sitecfg.get('nothing') is None
    from kicad2ems.solvers.lines.fastsolve import find_qucsator
    assert find_qucsator() == str(exe)                              # the recorded one, before the PATH
    from kicad2ems.solvers.emerge.plugin import emerge_python
    monkeypatch.delenv('KICAD2EMS_EMERGE_PYTHON', raising=False)
    assert emerge_python(None) == '/opt/emerge/bin/python'
    monkeypatch.setattr(sitecfg, '_cache', None)
