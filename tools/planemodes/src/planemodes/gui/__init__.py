"""The Qt viewer (PySide6 + pyqtgraph)."""
import sys


def main(path=None):
    """Open the viewer, optionally with a board loaded."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication
    from .app import MainWindow
    pg.setConfigOptions(antialias=True, imageAxisOrder='row-major')
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow(path)
    w.show()
    return app.exec()
