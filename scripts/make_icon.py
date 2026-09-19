"""生成 MAgent 应用图标 assets/magent.ico（品牌蓝底 + 白色 M，多尺寸）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "magent.ico"

BRAND = (47, 95, 224)      # #2f5fe0
BRAND_DARK = (36, 71, 170)
SIZE = 256
RADIUS = 56


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角底：上浅下深，模拟品牌蓝渐变
    draw.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=RADIUS, fill=BRAND_DARK)
    draw.rounded_rectangle([8, 8, SIZE - 8, SIZE - 22], radius=RADIUS, fill=BRAND)

    # 白色 M（微软雅黑粗体，居中）
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 150)
    text = "M"
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((SIZE - (right - left)) / 2 - left, (SIZE - (bottom - top)) / 2 - top - 6),
              text, font=font, fill=(255, 255, 255, 255))

    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"图标已生成：{OUT}")


if __name__ == "__main__":
    main()
