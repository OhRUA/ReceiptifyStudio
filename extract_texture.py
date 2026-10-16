# -*- coding: utf-8 -*-
# 真实皱纸照片纸面提取（双版本）：
#   A. 纯光影场（R=50）：折痕柔和光影，纸面由渲染层加合成纤维
#   B. 真实纸面打底（R=14）：保留真实纸纤维肌理 + 更强文字补偿
#      纸面自带真实照片的纤维/颗粒质感，折痕更"原生"
# 输出：
#   paper_texture.png / paper_texture_b.png  —— A 版（原图 + 翻转变体）
#   paper_texture_c.png / paper_texture_d.png —— B 版（原图 + 翻转变体）
from PIL import Image, ImageChops, ImageFilter
import os, statistics

REF = r"assets\ref_real.png"                # 素材用相对路径（仓库自洽，可直接运行）
OUT_DIR = r"assets"

img = Image.open(REF).convert("L")
w, h = img.size
ink = img.point(lambda v: 255 if v < 150 else 0)    # 墨迹掩膜（文字=白）


def build_field(R, K, out_name, base_lum=224):
    """提取纸面：强/中模糊（抹文字、留折痕）+ 文字密度补偿"""
    light = img.filter(ImageFilter.GaussianBlur(R))
    density = ink.filter(ImageFilter.GaussianBlur(R))
    field = ImageChops.add(light, density.point(lambda v: int(v * K)))
    vals = list(field.getdata())
    mean = statistics.mean(vals)
    field = field.point(lambda v: max(0, min(255, int(v + (base_lum - mean)))))
    field.save(os.path.join(OUT_DIR, out_name))
    vals = list(field.getdata())
    print(f"{out_name}: 均值 {statistics.mean(vals):.1f}, std {statistics.pstdev(vals):.1f}")
    return field


# ---- A 版：纯光影场（R=50，柔和折痕，纸面平滑） ----
a = build_field(50, 0.70, "paper_texture.png")
a.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.FLIP_LEFT_RIGHT).save(
    os.path.join(OUT_DIR, "paper_texture_b.png"))
print("paper_texture_b.png 已保存（A 翻转变体）")

# ---- B 版：真实纸面打底（R=14，保留纸纤维肌理，补偿更强） ----
b = build_field(14, 1.10, "paper_texture_c.png")
b.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.FLIP_LEFT_RIGHT).save(
    os.path.join(OUT_DIR, "paper_texture_d.png"))
print("paper_texture_d.png 已保存（B 翻转变体）")
print("完成：A 版(纯光影场) + B 版(真实纸面打底)")
