"""Design tokens.

Direction: a drafting office. Cool drafting-film greys, navy drawing ink,
and a single redline colour reserved for anything that will be masked —
the same red people use when they mark up a drawing by hand.
"""

FONT = "IBM Plex Sans"
FONT_CONDENSED = "IBM Plex Sans Condensed"

FILM = "#E9EDF1"      # app background, drafting film
SHEET = "#FFFFFF"     # panels
INK = "#1F2B38"       # primary text, primary button
INK_SOFT = "#34475A"  # hover on ink
MUTED = "#5E6B79"     # secondary text
RULE = "#CBD3DB"      # borders and dividers
TABLE = "#3B4652"     # preview backdrop, the drawing table
REDLINE = "#C62828"   # masks only


def stylesheet(assets: str) -> str:
    """Build the stylesheet; `assets` is the asset folder as a forward-slash path."""
    return f"""
* {{
    font-family: "{FONT}", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: {INK};
}}
QMainWindow, #Root {{ background: {FILM}; }}

#Sidebar {{
    background: {SHEET};
    border-right: 1px solid {RULE};
}}
#StepTitle {{ font-weight: 600; font-size: 10.5pt; }}
#StepNumber {{
    color: {SHEET}; background: {INK};
    border-radius: 10px; min-width: 20px; max-width: 20px;
    min-height: 20px; max-height: 20px;
    font-size: 9pt; font-weight: 600; qproperty-alignment: AlignCenter;
}}
#Hint {{ color: {MUTED}; font-size: 9pt; }}
#Divider {{ background: {RULE}; max-height: 1px; min-height: 1px; }}

QLineEdit {{
    background: {FILM}; border: 1px solid {RULE}; border-radius: 4px;
    padding: 6px 8px; selection-background-color: {INK};
}}
QLineEdit:focus {{ border: 1px solid {INK}; background: {SHEET}; }}

QPushButton {{
    background: {SHEET}; border: 1px solid {RULE}; border-radius: 4px;
    padding: 6px 12px;
}}
QPushButton:hover {{ border-color: {INK}; }}
QPushButton:focus {{ border: 1px solid {INK}; }}
QPushButton:disabled {{ color: #9AA5B1; border-color: {RULE}; }}

QPushButton#Primary {{
    background: {INK}; color: {SHEET}; border: none;
    font-weight: 600; font-size: 11pt; padding: 11px 16px; border-radius: 5px;
}}
QPushButton#Primary:hover {{ background: {INK_SOFT}; }}
QPushButton#Primary:disabled {{ background: #8F9BA8; color: #E9EDF1; }}

QPushButton#Ghost {{ border: none; background: transparent; color: {MUTED}; padding: 4px 6px; }}
QPushButton#Ghost:hover {{ color: {INK}; text-decoration: underline; }}

QListWidget {{
    background: {SHEET}; border: 1px solid {RULE}; border-radius: 4px;
    outline: none; padding: 2px;
}}
QListWidget::item {{ padding: 6px 6px; border-radius: 3px; }}
QListWidget::item:hover {{ background: {FILM}; }}
QListWidget::item:selected {{ background: {INK}; color: {SHEET}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {MUTED}; border-radius: 3px; background: {SHEET};
}}
QCheckBox::indicator:checked {{ background: {INK}; border-color: {INK}; image: url({assets}/check.svg); }}
QCheckBox::indicator:focus {{ border-color: {INK}; }}

QComboBox {{
    background: {SHEET}; border: 1px solid {RULE}; border-radius: 4px; padding: 5px 8px;
}}
QComboBox:focus {{ border-color: {INK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({assets}/chevron.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SHEET}; selection-background-color: {INK}; selection-color: {SHEET}; }}

QProgressBar {{
    background: {FILM}; border: none; border-radius: 2px; max-height: 4px; text-align: center;
}}
QProgressBar::chunk {{ background: {INK}; border-radius: 2px; }}

#Toolbar {{ background: {SHEET}; border-bottom: 1px solid {RULE}; }}
#PageLabel {{ color: {MUTED}; }}
#FileTitle {{ font-weight: 600; }}

#FilesPanel {{ background: {SHEET}; border-left: 1px solid {RULE}; }}
QListWidget#FileList {{ border: none; border-radius: 0; padding: 4px 8px; }}

#Table {{ background: {TABLE}; border: none; }}

QStatusBar {{ background: {SHEET}; border-top: 1px solid {RULE}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; font-size: 9pt; }}

QPlainTextEdit#Log {{
    background: {FILM}; border: 1px solid {RULE}; border-radius: 4px;
    font-size: 9pt; color: {INK}; padding: 4px;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #AEB8C3; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #AEB8C3; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QMenuBar {{ background: {SHEET}; border-bottom: 1px solid {RULE}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {FILM}; }}
QMenu {{ background: {SHEET}; border: 1px solid {RULE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {INK}; color: {SHEET}; }}

QToolTip {{ background: {INK}; color: {SHEET}; border: none; padding: 5px 8px; }}
QMessageBox QLabel {{ min-width: 280px; }}
"""
