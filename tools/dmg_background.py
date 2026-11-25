"""Готовит фон окна Finder с инструкцией между реальными значками программы и папки."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parents[1] / "app" / "ui" / "assets" / "fonts"


def render(scale):
    image = Image.new("RGB", (600 * scale, 380 * scale), "#f4f7f7")
    draw = ImageDraw.Draw(image)
    for y, text, font, size, color in (
        (48, "Установка PDF-помощника", "Inter-SemiBold.ttf", 25, "#143138"),
        (87, "Перетащите приложение в папку «Программы»", "Inter-Regular.ttf", 15, "#435d64"),
    ):
        draw.text((300 * scale, y * scale), text, font=ImageFont.truetype(str(FONTS / font), size * scale),
                  fill=color, anchor="mt")
    for points in (((272, 200), (327, 200)), ((315, 187), (328, 200), (315, 213))):
        draw.line([(x * scale, y * scale) for x, y in points], fill="#007277", width=3 * scale, joint="curve")
    return image


if __name__ == "__main__":
    target = Path(sys.argv[1])
    for scale, suffix in ((1, ""), (2, "@2x")):
        render(scale).save(target / f"install{suffix}.png")
