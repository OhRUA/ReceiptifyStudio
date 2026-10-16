# -*- coding: utf-8 -*-
"""
make_icon.py — 生成 Receiptify Studio 的「迷你小票」程序图标（.ico）
设计：透明背景 + 白色锯齿小票 + 顶部黑条 + 横线纹理 + 底部条码
简洁克制，16px 小尺寸下依然清晰可辨（迷你感）
用法：python make_icon.py  → 生成 ReceiptifyStudio.ico + output/icon_preview.png
"""
import os
import random

from PIL import Image, ImageDraw

SIZE = 256  # 基准尺寸（.ico 会按此缩放多尺寸）

# ---------- 颜色 ----------
PAPER = (248, 246, 240)      # 米白纸
INK = (26, 26, 26)           # 墨色（黑条/文字/条码）
GREY = (96, 92, 84)          # 次级灰（横线/折痕）


def main():
    # 透明画布（迷你图标：无背景卡，只有小票本体）
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rng = random.Random(7)

    # 小票本体：圆角矩形，居中，占画布 88%（留一点呼吸空间）
    rw, rh = int(SIZE * 0.80), int(SIZE * 0.86)
    rx = (SIZE - rw) // 2
    ry = (SIZE - rh) // 2
    tooth = int(SIZE * 0.045)              # 锯齿大小（随基准缩放）

    draw.rounded_rectangle([rx, ry, rx + rw, ry + rh], radius=int(SIZE * 0.03), fill=PAPER)

    # 左右锯齿撕边：用三角缺口挖出撕边（背景透明）
    y = ry
    while y < ry + rh:
        draw.polygon([(rx, y), (rx + tooth, y + tooth // 2), (rx, y + tooth)], fill=(0, 0, 0, 0))
        draw.polygon([(rx + rw, y), (rx + rw - tooth, y + tooth // 2), (rx + rw, y + tooth)], fill=(0, 0, 0, 0))
        y += tooth

    # 顶部黑条（小票黑色抬头区）
    bar_h = int(SIZE * 0.055)
    draw.rounded_rectangle([rx + 4, ry + 4, rx + rw - 4, ry + 4 + bar_h], radius=3, fill=INK)

    # 中部横线纹理（打印行感，2 条细线）
    lw = int(SIZE * 0.012)
    for i in range(2):
        yy = ry + int(SIZE * 0.18) + i * int(SIZE * 0.085)
        draw.line([(rx + int(SIZE * 0.08), yy), (rx + rw - int(SIZE * 0.08), yy)],
                  fill=GREY, width=lw)

    # 一条浅折痕（斜向，复古感，很淡）
    draw.line([(rx + int(SIZE * 0.2), ry + int(SIZE * 0.42)),
               (rx + int(SIZE * 0.62), ry + int(SIZE * 0.56))],
              fill=(170, 160, 145, 150), width=int(SIZE * 0.016))

    # 底部条码：粗细交替竖线
    bb = ry + int(SIZE * 0.60)
    bh = int(SIZE * 0.18)
    bw = rx + int(SIZE * 0.14)
    while bw < rx + rw - int(SIZE * 0.14):
        bar_w = rng.choice([2, 3, 5])
        draw.rectangle([bw, bb, bw + bar_w, bb + bh], fill=INK)
        bw += bar_w + rng.choice([1, 2, 4])

    # 底部撕边（下边缘锯齿）
    y = bb + bh + int(SIZE * 0.03)
    while y < ry + rh - 4:
        draw.polygon([(rx, y), (rx + tooth, y + tooth // 2), (rx, y + tooth)], fill=(0, 0, 0, 0))
        draw.polygon([(rx + rw, y), (rx + rw - tooth, y + tooth // 2), (rx + rw, y + tooth)], fill=(0, 0, 0, 0))
        y += tooth

    # 保存多尺寸 .ico（16~256，Windows 各场景都清晰）
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "ReceiptifyStudio.ico")
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"迷你小票图标已生成：{out}")

    # 输出 PNG 预览（放大 2x 便于查看）
    os.makedirs(os.path.join(here, "output"), exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(here, "output", "icon_preview.png"))
    print("预览图：output/icon_preview.png")


if __name__ == "__main__":
    main()
