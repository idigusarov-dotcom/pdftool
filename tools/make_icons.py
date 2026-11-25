"""Экспортирует мастер-изображение PdfTool_1024.png в значки приложения и установщика."""
from pathlib import Path

from PIL import Image, ImageOps

ASSETS = Path(__file__).resolve().parents[1] / "app" / "ui" / "assets"


def main():
    with Image.open(ASSETS / "PdfTool_1024.png") as source:
        mac = source.convert("RGBA")
    if mac.size != (1024, 1024):
        raise ValueError("Мастер-изображение должно быть 1024 × 1024 пикселя")
    mac.save(ASSETS / "PdfTool.icns")
    # В Dock сохраняем внешние поля. В Проводнике и панели инструментов заполняем ячейку плотнее.
    face = mac.crop(mac.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0).getbbox())
    win = ImageOps.contain(face, (244, 244), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (256, 256))
    tile.alpha_composite(win, ((256 - win.width) // 2, (256 - win.height) // 2))
    tile.save(ASSETS / "PdfTool.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    tile.save(ASSETS / "icons" / "app-logo.png", optimize=True)


if __name__ == "__main__":
    main()
