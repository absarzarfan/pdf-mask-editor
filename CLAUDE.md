# PDF Mask Editor

Aplikasi desktop Windows (PySide6 + PyMuPDF) untuk menutup area tertentu
di banyak PDF sekaligus.

## Struktur
- pdfmask/core.py: mesin redaksi, tanpa UI, wajib ada tes
- pdfmask/canvas.py, pagestrip.py, main_window.py, splash.py, theme.py: UI
- tests/: pytest

## Aturan
- Jalankan `python -m pytest -q` setelah mengubah core.py
- Semua teks UI dalam bahasa Inggris, kalimat biasa, tanpa huruf kapital semua
- Warna merah (REDLINE) hanya untuk area mask
- Kredit "PDF Mask Editor by @absarz software" harus tetap ada di splash,
  status bar, dan About
- Jangan pernah menambahkan atau meng-commit file PDF asli
