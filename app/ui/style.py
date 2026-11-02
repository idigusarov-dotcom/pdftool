"""Оформление: шрифт Inter (OFL), иконки Lucide (ISC), светлая/тёмная тема, общий QSS."""
from __future__ import annotations

import os
import sys
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def asset(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "app", "ui", "assets", *parts)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", *parts)


LIGHT = dict(bg="#f4f6fb", surface="#ffffff", surface2="#f8f9fc", border="#e2e6ef", text="#1d2330",
             muted="#6b7385", accent="#2f6bff", accent_hover="#1f56e0", accent_soft="#e8efff",
             success="#16a34a", success_soft="#e7f6ec", warn="#d97706", warn_soft="#fff4e0",
             danger="#dc2626", danger_soft="#fde8e8", sidebar="#ffffff", sel="#e8efff", sel_text="#1d2330",
             dim="rgba(15,20,35,150)")
DARK = dict(bg="#14161b", surface="#1d2027", surface2="#232731", border="#2e3340", text="#e7e9ef",
            muted="#9aa2b3", accent="#5b8cff", accent_hover="#7aa2ff", accent_soft="#24304d",
            success="#3fbf6a", success_soft="#1d3326", warn="#f0a43a", warn_soft="#3a2e1a",
            danger="#f06262", danger_soft="#3d2224", sidebar="#181b21", sel="#24304d", sel_text="#e7e9ef",
            dim="rgba(0,0,0,170)")

_state = {"dark": False}


def colors() -> dict:
    return DARK if _state["dark"] else LIGHT


def is_dark() -> bool:
    return _state["dark"]


def load_fonts() -> str:
    """Регистрирует Inter. Возвращает имя семейства (Inter или системный запасной)."""
    fam = None
    for fn in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf"):
        i = QFontDatabase.addApplicationFont(asset("fonts", fn))
        if i >= 0 and not fam:
            fams = QFontDatabase.applicationFontFamilies(i)
            fam = fams[0] if fams else None
    return fam or ("Segoe UI" if sys.platform.startswith("win") else "Sans Serif")


@lru_cache(maxsize=512)
def _pixmap(name: str, color: str, size: int) -> QPixmap:
    with open(asset("icons", name + ".svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", color)
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: str | None = None, size: int = 64) -> QIcon:
    """Иконка Lucide нужного цвета (по умолчанию — цвет текста темы)."""
    if not name:
        return QIcon()
    try:
        return QIcon(_pixmap(name, color or colors()["text"], size))
    except OSError:
        return QIcon()


def qss() -> str:
    c = colors()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['text']}; font-size: 10pt; }}
QMainWindow, QStackedWidget, QScrollArea, #Page {{ background: {c['bg']}; }}
QDialog {{ background: {c['surface']}; }}
QToolTip {{ background: {c['text']}; color: {c['surface']}; border: none; padding: 6px 8px; border-radius: 6px; }}
#Header {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
#AppTitle {{ font-size: 13pt; font-weight: 600; }}
#AppVersion {{ color: {c['muted']}; }}
#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#Sidebar QPushButton {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px;
    font-size: 10.5pt; background: transparent; color: {c['text']}; }}
#Sidebar QPushButton:hover {{ background: {c['surface2']}; }}
#Sidebar QPushButton:checked {{ background: {c['sel']}; color: {c['accent']}; font-weight: 600; }}
#ScreenTitle {{ font-size: 17pt; font-weight: 600; }}
#ScreenSub {{ color: {c['muted']}; font-size: 10pt; }}
#Card, QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QGroupBox {{ margin-top: 18px; padding: 14px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; color: {c['muted']}; }}
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 9px;
    padding: 7px 14px; min-height: 22px; }}
QPushButton:hover {{ background: {c['surface2']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['accent_soft']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton[kind="primary"] {{ background: {c['accent']}; color: white; border: 1px solid {c['accent']};
    font-weight: 600; padding: 9px 20px; font-size: 10.5pt; }}
QPushButton[kind="primary"]:hover {{ background: {c['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['border']}; border-color: {c['border']}; color: {c['muted']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {c['text']}; }}
QPushButton[kind="ghost"]:hover {{ background: {c['surface2']}; border-color: {c['border']}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
QToolButton:hover {{ background: {c['surface2']}; border-color: {c['border']}; }}
QToolButton#HelpButton {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; padding: 5px 10px; }}
QLineEdit, QSpinBox, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 5px 8px; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']};
    selection-background-color: {c['sel']}; selection-color: {c['sel_text']}; }}
QTableWidget, QListWidget, QTextBrowser, QTextEdit {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: {c['border']}; selection-background-color: {c['sel']};
    selection-color: {c['sel_text']}; alternate-background-color: {c['surface2']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}
QGraphicsView {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; text-align: center;
    min-height: 18px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 7px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['surface']}; border: 2px solid {c['accent']}; width: 14px;
    height: 14px; margin: -7px 0; border-radius: 9px; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['sel']}; }}
#StepBar {{ background: transparent; }}
#Step {{ border-radius: 16px; padding: 4px 12px 4px 4px; }}
#Step[state="active"] {{ background: {c['accent_soft']}; }}
#StepNum {{ border-radius: 12px; min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
    font-weight: 600; qproperty-alignment: AlignCenter; background: {c['border']}; color: {c['muted']}; }}
#Step[state="active"] #StepNum {{ background: {c['accent']}; color: white; }}
#Step[state="done"] #StepNum {{ background: {c['success']}; color: white; }}
#StepText {{ color: {c['muted']}; }}
#Step[state="active"] #StepText {{ color: {c['text']}; font-weight: 600; }}
#Step[state="done"] #StepText {{ color: {c['text']}; }}
#StepArrow {{ color: {c['border']}; }}
#DropZone {{ background: {c['surface']}; border: 2px dashed {c['border']}; border-radius: 12px; }}
#DropZone[hover="true"] {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
#DropZone[loaded="true"] {{ border: 1px solid {c['border']}; }}
#DropTitle {{ font-weight: 600; }}
#DropSub {{ color: {c['muted']}; }}
#Advanced {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
#AdvancedHead {{ text-align: left; border: none; background: transparent; font-weight: 600; padding: 8px 10px; }}
#Toast {{ background: {c['text']}; border-radius: 12px; }}
#Toast QLabel {{ color: {c['surface']}; }}
#Toast QPushButton {{ background: transparent; color: {c['accent'] if not _state['dark'] else '#1d2330'};
    border: none; font-weight: 600; padding: 4px 8px; }}
#Toast[kind="error"] {{ background: {c['danger']}; }}
#Toast[kind="error"] QLabel {{ color: white; }}
#TourBubble {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
#TourTitle {{ font-size: 12.5pt; font-weight: 600; }}
#TourText {{ font-size: 10.5pt; }}
#TourCount {{ color: {c['muted']}; }}
#Muted {{ color: {c['muted']}; }}
#InkPreview {{ background: white; border: 1px solid {c['border']}; border-radius: 8px; }}
"""


def apply_theme(app, dark: bool = False):
    _state["dark"] = bool(dark)
    _pixmap.cache_clear()
    app.setStyle("Fusion")
    app.setStyleSheet(qss())


# цвета пометок в таблицах (светлая / тёмная тема)
_MARKS = {"white": ("#ffffff", "#1d2027"), "#ffe082": ("#ffe082", "#5a4a17"), "#eeeeee": ("#eeeeee", "#2a2e37"),
          "#fff8e1": ("#fff8e1", "#3a3420"), "#ffcdd2": ("#ffcdd2", "#5a2a2e"), "#ffcc80": ("#ffcc80", "#5e4220"),
          "#c8e6c9": ("#c8e6c9", "#1f4a2c")}


def mark(name: str):
    from PySide6.QtGui import QColor
    light, dark = _MARKS.get(name, (name, name))
    return QColor(dark if is_dark() else light)
