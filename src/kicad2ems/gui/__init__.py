"""kicad2ems GUI (needs PySide6 and pyqtgraph: pip install 'kicad2ems[gui]')."""


def application(argv=()):
    """The QApplication, made with shared OpenGL contexts when there is none yet: the 3D field view's shaders and
    buffers (pyqtgraph caches them across views) stay valid when a dock is moved or another window opens."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        app = QApplication(list(argv))
        import os
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kicad2ems.svg')))
        app.setDesktopFileName('kicad2ems')                         # Wayland: the window's launcher entry and icon
    return app


def main(argv=None):
    """kicad2ems-gui [config.toml]"""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from .app import main as _main
    except ImportError as e:
        raise SystemExit(f"kicad2ems-gui needs PySide6 and pyqtgraph ({e}): python -m pip install 'kicad2ems[gui]'")
    return _main(argv)
