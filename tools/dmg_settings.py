# Настройки окна .dmg для dmgbuild (без AppleScript и Finder — надёжно на CI):
# слева значок программы, справа ярлык папки «Программы», между ними стрелка.
import os

app = defines.get("app", "dist/PdfTool.app")  # noqa: F821 — переменную defines подставляет dmgbuild
files = [app]
symlinks = {"Программы": "/Applications"}
icon_locations = {os.path.basename(app): (150, 185), "Программы": (450, 185)}
background = "builtin-arrow"
window_rect = ((200, 160), (600, 380))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 112
text_size = 13
format = "UDZO"
filesystem = "HFS+"
badge_icon = None
icon = defines.get("icon")  # noqa: F821
