@echo off
rem kicad2ems setup on Windows: install.py does it all (setup.cmd --help); this runs it with the py launcher,
rem else python on the PATH.
setlocal
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% "%~dp0install.py" %*
exit /b %ERRORLEVEL%
