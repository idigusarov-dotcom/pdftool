# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller pdftool.spec
#   Windows -> dist/PdfTool.exe (один файл)
#   macOS   -> dist/PdfTool.app (папка-приложение; в .dmg её упаковывает tools/make_dmg.sh)
import os
import re
import sys

IS_MAC = sys.platform == "darwin"
VERSION = re.search(r'__version__ = "([^"]+)"', open(os.path.join("app", "__init__.py"), encoding="utf-8").read()).group(1)
SHORT_VERSION = re.match(r"\d+(\.\d+)*", VERSION).group(0)      # 1.4.0-beta1 -> 1.4.0 (так требует macOS)

a = Analysis(
    ["run.py"],
    pathex=["."],
    hiddenimports=["pymupdf", "PySide6.QtSvg"],
    datas=[("app/core/models/digits_cnn.npz", "app/core/models"),
           ("app/ui/assets/fonts", "app/ui/assets/fonts"),
           ("app/ui/assets/icons", "app/ui/assets/icons")],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtNetwork", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "pytest",
              "pymupdf.layout", "pymupdf4llm", "onnxruntime", "cv2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if not IS_MAC:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="PdfTool",
        icon="app/ui/assets/PdfTool.ico",
        console=False,
        upx=False,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="PdfTool",
        console=False,
        upx=False,
        argv_emulation=False,          # файлы из Finder/Dock приходят событием FileOpen (см. app/main.py)
        target_arch=os.environ.get("PDFTOOL_ARCH") or None,
        codesign_identity=None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="PdfTool", upx=False)
    app = BUNDLE(
        coll,
        name="PdfTool.app",
        icon="app/ui/assets/PdfTool.icns",
        bundle_identifier="ru.akvatehstroy.pdftool",
        version=SHORT_VERSION,
        info_plist={
            "CFBundleName": "PDF-помощник",
            "CFBundleDisplayName": "PDF-помощник",
            "CFBundleExecutable": "PdfTool",
            "CFBundleShortVersionString": SHORT_VERSION,
            "CFBundleVersion": SHORT_VERSION,
            "CFBundleDevelopmentRegion": "ru",
            "CFBundleLocalizations": ["ru"],
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,       # тёмная тема macOS
            "NSSupportsAutomaticGraphicsSwitching": True,
            "NSHumanReadableCopyright": "АкваТехСтрой. Шрифт Inter (SIL OFL 1.1), иконки Lucide (ISC).",
            "PdfToolFullVersion": VERSION,
            "CFBundleDocumentTypes": [
                {"CFBundleTypeName": "PDF-документ", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate",
                 "LSItemContentTypes": ["com.adobe.pdf"]},
                {"CFBundleTypeName": "Картинка (печать, подпись)", "CFBundleTypeRole": "Viewer",
                 "LSHandlerRank": "Alternate", "LSItemContentTypes": ["public.png", "public.jpeg"]},
            ],
        },
    )
