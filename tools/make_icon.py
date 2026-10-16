# -*- coding: utf-8 -*-
"""生成应用图标 assets/app.ico（多尺寸）与预览图 assets/app-256.png。

纯离线（Pillow），不依赖任何设计文件。改了配色/形状后重跑即可，
PyInstaller 的 spec 会自动引用 assets/app.ico。

用法:
    python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
S = 1024
SS = 4                      # 超采样倍数，边缘更干净

BG_OUT = (37, 99, 235, 255)     # 品牌蓝（外圈）
BG_IN = (14, 19, 32, 255)       # 深色底（与界面深色主题一致）
RING = (32, 190, 200, 255)      # 青色描边
TICK = (56, 224, 140, 255)      # 成功绿对勾


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    w = S * SS
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((0, 0, w, w), radius=int(w * 0.22), fill=BG_OUT)
    d.rounded_rectangle((int(w * 0.055), int(w * 0.055),
                         int(w * 0.945), int(w * 0.945)),
                        radius=int(w * 0.18), fill=BG_IN)
    d.rounded_rectangle((int(w * 0.075), int(w * 0.075),
                         int(w * 0.925), int(w * 0.925)),
                        radius=int(w * 0.165), fill=RING)
    d.rounded_rectangle((int(w * 0.095), int(w * 0.095),
                         int(w * 0.905), int(w * 0.905)),
                        radius=int(w * 0.15), fill=BG_IN)

    stroke = int(w * 0.075)
    pts = [(int(w * 0.30), int(w * 0.53)),
           (int(w * 0.45), int(w * 0.68)),
           (int(w * 0.72), int(w * 0.34))]
    d.line(pts, fill=TICK, width=stroke, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0] - stroke // 2, p[1] - stroke // 2,
                   p[0] + stroke // 2, p[1] + stroke // 2), fill=TICK)

    img = img.resize((S, S), Image.LANCZOS)
    img.save(OUT / "app.ico", format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48),
                    (32, 32), (16, 16)])
    img.resize((256, 256), Image.LANCZOS).save(OUT / "app-256.png")
    print(f"已生成 {OUT / 'app.ico'}（{(OUT / 'app.ico').stat().st_size} B）"
          f" 与 {OUT / 'app-256.png'}")


if __name__ == "__main__":
    main()
