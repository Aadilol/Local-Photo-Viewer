"""Photo Viewer launcher (Windows, macOS, Linux)."""
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photoviewer.preferences import ORGANIZATION, APPLICATION
from photoviewer.diagnostics import initialize_diagnostics
from photoviewer.ui import MainWindow


def main():
    initialize_diagnostics()
    # Windows DPI scaling is on by default in Qt 6.
    app = QApplication(sys.argv)
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(APPLICATION)
    app.setApplicationDisplayName(APPLICATION)
    app.setStyle('Fusion')
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / 'photoviewer' / 'assets' / 'PhotoViewer.ico')))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
