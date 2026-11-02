# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller pdftool.spec  ->  dist/PdfTool(.exe)
a = Analysis(
    ["run.py"],
    pathex=["."],
    hiddenimports=["pymupdf"],
    datas=[("app/core/models/digits_cnn.npz", "app/core/models")],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtNetwork", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "pytest", "pymupdf.layout", "pymupdf4llm", "onnxruntime", "cv2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PdfTool",
    console=False,
    upx=False,
)
