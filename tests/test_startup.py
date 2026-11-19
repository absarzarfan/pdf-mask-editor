import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pdfmask.main_window import MainWindow


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_settings(tmp_path):
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    yield
    QSettings("absarz software", "PDF Mask Editor").clear()


def test_starts_empty_and_purges_old_keys(qapp, isolated_settings, tmp_path):
    import pymupdf
    pdf = tmp_path / "sample.pdf"
    doc = pymupdf.open(); doc.new_page(); doc.save(str(pdf)); doc.close()

    s = QSettings("absarz software", "PDF Mask Editor")
    s.setValue("layouts", '{"layouts": [{"name": "Layout 1", "pages": "1-30", "areas": [[0.1, 0.1, 0.5, 0.5]]}]}')
    s.setValue("areas", '[[0.1, 0.1, 0.5, 0.5]]')
    s.setValue("source_file", str(pdf))
    s.setValue("source", str(tmp_path))
    s.setValue("fill", "Black")
    s.setValue("last_file", str(pdf))
    s.setValue("output", str(tmp_path / "masked"))
    s.sync()

    win = MainWindow()
    try:
        assert win.file_src is None
        assert win.layouts == []
        assert win.files == []
        assert not win.dir_src and not win.dir_out
        s2 = QSettings("absarz software", "PDF Mask Editor")
        for key in ("layouts", "areas", "source_file", "source", "output", "fill", "last_file"):
            assert not s2.contains(key)
        assert not win.run_btn.isEnabled()
        assert win.status_msg.text() == "Choose a drawing file to begin."
    finally:
        win.close()
