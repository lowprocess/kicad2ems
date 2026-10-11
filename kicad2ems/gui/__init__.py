"""kicad2ems GUI (needs PySide6 and pyqtgraph: pip install 'kicad2ems[gui]')."""


def main(argv=None):
    try:
        from .app import main as _main
    except ImportError as e:
        raise SystemExit(f"kicad2ems-gui needs PySide6 and pyqtgraph ({e}): python -m pip install 'kicad2ems[gui]'")
    return _main(argv)
