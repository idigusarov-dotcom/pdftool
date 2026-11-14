"""Оформление в духе Apple HIG: системный шрифт (SF Pro на Mac, Segoe UI на Windows, Inter — запасной),
системный синий акцент, светлая/тёмная тема по настройке системы, скруглённые карточки. Иконки Lucide (ISC).

Размеры шрифтов в QSS заданы в пикселях: на macOS 1pt = 1px (72 dpi), на Windows 1pt = 1.33px,
поэтому «10pt» выглядел бы на Mac мелким. Пиксели одинаковы на обеих системах.
"""
from __future__ import annotations

import sys
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..core.resources import resource

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


def asset(*parts: str) -> str:
    return resource("ui", "assets", *parts)


# Цвета близки к системным цветам macOS (systemBlue, labelColor, separatorColor …), но свои значения.
LIGHT = dict(bg="#ffffff", surface="#ffffff", surface2="#f5f5f7", border="#e3e3e8", border_strong="#d2d2d7",
             text="#1d1d1f", muted="#6e6e73", accent="#007aff", accent_hover="#0066d6", accent_soft="#e6f0ff",
             success="#28a745", success_soft="#e8f6ec", warn="#c76a00", warn_soft="#fff3e0",
             danger="#e5372b", danger_soft="#fdeceb", sidebar="#f3f3f6", sel="#e6f0ff", sel_text="#1d1d1f",
             fill="rgba(0,0,0,0.055)", fill_hover="rgba(0,0,0,0.09)", page="#ececf0",
             dim="rgba(15,20,35,150)")
DARK = dict(bg="#1e1e20", surface="#2a2a2d", surface2="#323236", border="#3a3a3e", border_strong="#4a4a4f",
            text="#f5f5f7", muted="#98989f", accent="#0a84ff", accent_hover="#409cff", accent_soft="#1c3556",
            success="#30d158", success_soft="#1d3326", warn="#ff9f0a", warn_soft="#3a2e1a",
            danger="#ff453a", danger_soft="#3d2224", sidebar="#262628", sel="#1c3556", sel_text="#f5f5f7",
            fill="rgba(255,255,255,0.08)", fill_hover="rgba(255,255,255,0.13)", page="#18181a",
            dim="rgba(0,0,0,170)")

_state = {"dark": False}


def colors() -> dict:
    return DARK if _state["dark"] else LIGHT


def is_dark() -> bool:
    return _state["dark"]


def system_dark() -> bool:
    """Тёмная ли тема в системе (macOS «Внешний вид», Windows «Режим приложений»)."""
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 — старый Qt или нет приложения
        return False


def _families() -> set:
    return set(QFontDatabase.families())


def load_fonts() -> str:
    """Регистрирует Inter (запасной шрифт) и возвращает семейство интерфейса:
    на Mac — системный SF Pro, на Windows — Segoe UI (Variable, если есть), иначе Inter."""
    inter = None
    for fn in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf"):
        i = QFontDatabase.addApplicationFont(asset("fonts", fn))
        if i >= 0 and not inter:
            fams = QFontDatabase.applicationFontFamilies(i)
            inter = fams[0] if fams else None
    if IS_MAC:
        return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
    if IS_WIN:
        fams = _families()
        for f in ("Segoe UI Variable Text", "Segoe UI"):
            if f in fams:
                return f
    return inter or QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()


def ui_font(family: str) -> QFont:
    f = QFont(family)
    f.setPixelSize(13)
    f.setHintingPreference(QFont.PreferNoHinting if IS_MAC else QFont.PreferDefaultHinting)
    return f


def key(text: str) -> str:
    """Подписи сочетаний клавиш под систему: на Mac «Ctrl» — это ⌘ (Qt сам так сопоставляет клавиши)."""
    if not IS_MAC or not text:
        return text
    for a, b in (("Ctrl + колесо мыши", "⌘ + колесо мыши (или щипок на трекпаде)"), ("Ctrl + колесо", "⌘ + колесо"),
                 ("Ctrl+колесо", "⌘ + колесо"), ("Ctrl + Shift + ", "⇧⌘"), ("Ctrl+Shift+", "⇧⌘"),
                 ("Ctrl + ", "⌘"), ("Ctrl+", "⌘"), ("Ctrl", "⌘")):
        text = text.replace(a, b)
    return text


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


def icon(name: str, color: str | None = None, size: int = 64, on_color: str | None = None) -> QIcon:
    """Иконка Lucide нужного цвета (по умолчанию — цвет текста темы). on_color — цвет во включённом
    состоянии (выбранный раздел боковой панели)."""
    if not name:
        return QIcon()
    try:
        ic = QIcon(_pixmap(name, color or colors()["text"], size))
        if on_color:
            ic.addPixmap(_pixmap(name, on_color, size), QIcon.Normal, QIcon.On)
        return ic
    except OSError:
        return QIcon()


def _arrow(name: str, color: str) -> str:
    """Стрелка для QSS (url() умеет только файлы): цветной PNG во временной папке, путь с «/»."""
    import os
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "PdfTool_ui")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{name}_{color.strip('#')}.png")
    if not os.path.exists(p):
        pm = _pixmap(name, color, 32)
        pm.save(p, "PNG")
    return p.replace("\\", "/")


def qss() -> str:
    c = colors()
    try:
        down, up = _arrow("chevron-down", c["muted"]), _arrow("chevron-up", c["muted"])
        tick = _arrow("check", "#ffffff")
    except Exception:  # noqa: BLE001 — нет прав на временную папку: просто без стрелок
        down = up = tick = ""
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['text']}; font-size: 13px; }}
QMainWindow, QStackedWidget, QScrollArea, #Page, #Content {{ background: {c['bg']}; }}
QDialog {{ background: {c['bg']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    padding: 5px 8px; border-radius: 6px; }}
#Toolbar {{ background: {c['bg']}; border-bottom: 1px solid {c['border']}; }}
#AppTitle {{ font-size: 15px; font-weight: 600; }}
#AppVersion {{ color: {c['muted']}; font-size: 11px; }}
#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#Sidebar QPushButton {{ text-align: left; padding: 7px 10px; border: none; border-radius: 7px;
    font-size: 13px; background: transparent; color: {c['text']}; }}
#Sidebar QPushButton:hover {{ background: {c['fill']}; }}
#Sidebar QPushButton:checked {{ background: {c['accent']}; color: white; font-weight: 600; }}
#Sidebar[compact="true"] QPushButton {{ text-align: center; padding: 8px 0; }}
#SideSection {{ color: {c['muted']}; font-size: 11px; font-weight: 600; padding: 10px 10px 2px 10px; }}
#ScreenTitle {{ font-size: 22px; font-weight: 700; }}
#ScreenSub {{ color: {c['muted']}; font-size: 13px; }}
#Card, QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QGroupBox {{ margin-top: 0px; padding: 30px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: border; subcontrol-position: top left; left: 12px; top: 9px;
    padding: 0; color: {c['text']}; font-size: 13px; }}
QPushButton {{ background: {c['fill']}; border: 1px solid transparent; border-radius: 8px;
    padding: 6px 14px; min-height: 20px; color: {c['text']}; }}
QPushButton:hover {{ background: {c['fill_hover']}; }}
QPushButton:pressed {{ background: {c['border_strong']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface2']}; }}
QPushButton[kind="primary"] {{ background: {c['accent']}; color: white; font-weight: 600; padding: 7px 18px; }}
QPushButton[kind="primary"]:hover {{ background: {c['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['border']}; color: {c['muted']}; }}
QPushButton[kind="ghost"] {{ background: transparent; color: {c['accent']}; }}
QPushButton[kind="ghost"]:hover {{ background: {c['fill']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 5px; }}
QToolButton:hover {{ background: {c['fill']}; }}
QToolButton:pressed {{ background: {c['fill_hover']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#HelpButton {{ color: {c['accent']}; font-weight: 600; padding: 5px 10px; }}
QToolButton#HelpButton:hover {{ background: {c['accent_soft']}; }}
QLineEdit, QSpinBox, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    border-radius: 7px; padding: 4px 8px; min-height: 20px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
QSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; background: transparent; width: 18px;
    subcontrol-origin: border; }}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 3px 2px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 2px 3px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {c['fill']}; border-radius: 4px; }}
QSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 4px; selection-background-color: {c['accent']}; selection-color: white; }}
QTableWidget, QListWidget, QTextBrowser, QTextEdit {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: {c['border']}; selection-background-color: {c['sel']};
    selection-color: {c['sel_text']}; alternate-background-color: {c['surface2']}; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px; font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}
QGraphicsView {{ background: {c['page']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ background: {c['fill']}; border: none; border-radius: 7px; text-align: center;
    min-height: 14px; max-height: 14px; font-size: 11px; color: {c['text']}; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 7px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; border: 1px solid {c['border_strong']}; width: 16px;
    height: 16px; margin: -7px 0; border-radius: 9px; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {c['border_strong']};
    background: {c['surface']}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url("{tick}"); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {c['surface2']}; }}
QRadioButton::indicator {{ width: 15px; height: 15px; border-radius: 8px; border: 1px solid {c['border_strong']};
    background: {c['surface']}; }}
QRadioButton::indicator:checked {{ border: 1px solid {c['accent']}; background: qradialgradient(cx:0.5, cy:0.5,
    radius:0.5, fx:0.5, fy:0.5, stop:0 white, stop:0.34 white, stop:0.42 {c['accent']}, stop:1 {c['accent']}); }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent']}; color: white; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
#StepBar {{ background: transparent; }}
#Step {{ border-radius: 14px; padding: 3px 10px 3px 3px; }}
#Step[state="active"] {{ background: {c['accent_soft']}; }}
#StepNum {{ border-radius: 11px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
    font-weight: 600; font-size: 12px; qproperty-alignment: AlignCenter; background: {c['fill_hover']};
    color: {c['muted']}; }}
#Step[state="active"] #StepNum {{ background: {c['accent']}; color: white; }}
#Step[state="done"] #StepNum {{ background: {c['success']}; color: white; }}
#StepText {{ color: {c['muted']}; }}
#Step[state="active"] #StepText {{ color: {c['text']}; font-weight: 600; }}
#Step[state="done"] #StepText {{ color: {c['text']}; }}
#StepArrow {{ color: {c['border_strong']}; }}
#DropZone {{ background: {c['surface2']}; border: 1.5px dashed {c['border_strong']}; border-radius: 12px; }}
#DropZone[hover="true"] {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
#DropZone[loaded="true"] {{ background: {c['surface']}; border: 1px solid {c['border']}; }}
#DropTitle {{ font-weight: 600; }}
#DropSub {{ color: {c['muted']}; font-size: 12px; }}
#Advanced {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
#AdvancedHead {{ text-align: left; border: none; background: transparent; font-weight: 600; padding: 7px 8px;
    color: {c['text']}; }}
#AdvancedHead:hover {{ background: transparent; color: {c['accent']}; }}
#Toast {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 12px; }}
#Toast QLabel {{ color: {c['text']}; }}
#Toast QPushButton {{ background: transparent; color: {c['accent']}; border: none; font-weight: 600;
    padding: 4px 8px; }}
#Toast QPushButton:hover {{ background: {c['fill']}; }}
#Toast[kind="error"] {{ background: {c['danger']}; border-color: {c['danger']}; }}
#Toast[kind="error"] QLabel, #Toast[kind="error"] QPushButton {{ color: white; }}
#TourBubble {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 14px; }}
#TourTitle {{ font-size: 16px; font-weight: 600; }}
#TourText {{ font-size: 13px; }}
#TourCount {{ color: {c['muted']}; }}
#Muted {{ color: {c['muted']}; }}
#Hint {{ color: {c['muted']}; font-size: 12px; font-weight: 400; }}
#InkPreview {{ background: white; border: 1px solid {c['border']}; border-radius: 8px; }}
"""


def apply_theme(app, dark: bool = False):
    _state["dark"] = bool(dark)
    _pixmap.cache_clear()
    app.setStyle("Fusion")
    from PySide6.QtGui import QColor, QPalette
    c = colors()
    pal = QPalette()
    for role, col in ((QPalette.Window, c["bg"]), (QPalette.Base, c["surface"]), (QPalette.AlternateBase, c["surface2"]),
                      (QPalette.Text, c["text"]), (QPalette.WindowText, c["text"]), (QPalette.ButtonText, c["text"]),
                      (QPalette.Button, c["surface2"]), (QPalette.Highlight, c["accent"]),
                      (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, c["surface"]),
                      (QPalette.ToolTipText, c["text"]), (QPalette.PlaceholderText, c["muted"]),
                      (QPalette.Link, c["accent"])):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(qss())


# цвета пометок в таблицах (светлая / тёмная тема)
_MARKS = {"white": ("#ffffff", "#2a2a2d"), "#ffe082": ("#ffe9a8", "#5a4a17"), "#eeeeee": ("#f0f0f2", "#333336"),
          "#fff8e1": ("#fff8e1", "#3a3420"), "#ffcdd2": ("#ffd6d3", "#5a2a2e"), "#ffcc80": ("#ffd9a0", "#5e4220"),
          "#c8e6c9": ("#d3f0d8", "#1f4a2c")}


def mark(name: str):
    from PySide6.QtGui import QColor
    light, dark = _MARKS.get(name, (name, name))
    return QColor(dark if is_dark() else light)
