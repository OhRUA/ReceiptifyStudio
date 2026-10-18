"""
receipt_render.py — 纯函数渲染引擎（Receiptify Studio 核心）

铁律：
1. 本模块只做「纯文本 → PIL 图片」，绝不 import tkinter，与 UI 彻底解耦。
2. 字体找不到 → 自动降级为默认字体，绝不闪退。
3. 资源路径兼容 PyInstaller 打包后的临时解压目录（sys._MEIPASS）。

纸张宽度：固定（FIXED_PAPER_W），内容超宽自动换行，不再撑宽画布。

字体策略（打印质感）：
  英文/数字/标点 → 等宽打印字体（mono），中文/日文 → CJK 字体，逐字符混合。
  想换成别的字体：把字体文件放进 assets/fonts/ 并命名为
  mono.ttf（英文等宽）/ cjk.ttf（中文），优先级最高。

文本排版语法（蓄水池里怎么写，这里就怎么画）：
  ;;      开头 → 注释行，不渲染（模板里写说明用）
  #       开头 → 居中粗体大标题        （如 #RECEIPTIFY）
  ##      开头 → 居中副标题            （如 ##LAST 6 MONTHS）
  ###     开头 → 居中灰色小字          （如 ###ORDER #0002 FOR NANCY）
  @平台名  开头 → 底部显示的平台        （如 @Spotify、@网易云音乐）
  --- = ~ 全符号行 → 虚线分隔线
  序号|歌名 - 歌手|时长 → 表格行（序号居左 / 内容居左 / 时长右对齐，内容自动换行）
  |续行内容|            → 表格延续行（序号留空，接上一行内容）
  键名|数值              → 信息行（键居左 / 数值右对齐，超宽自动换行）
  其他                    → 普通左对齐行（超宽自动换行）
"""

import math
import os
import random
import sys
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter
import barcode
from barcode.writer import ImageWriter


# ============================================================
# 零、纸张宽度常量（想调宽窄，只改这一个数）
# ============================================================
FIXED_PAPER_W = 660        # 固定纸张宽度（像素）

# ============================================================
# 一、资源路径：兼容 PyInstaller 打包
# ============================================================
def resource_path(relative: str) -> str:
    """
    把相对路径转成绝对路径。
    打包成 exe 后资源会被解压到 sys._MEIPASS 临时目录，
    开发时则用本文件所在目录，两种情况都能正确找到资源。
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


# ============================================================
# 二、字体加载：等宽打印体 + CJK 中文，双字体候选，失败降级
# ============================================================
# 英文/数字用的等宽打印字体（收银机/打字机质感）
_MONO_CANDIDATES = [
    "assets/fonts/mono.ttf",                                  # 用户自放（最高优先）
    "C:/Windows/Fonts/consola.ttf",                           # Windows 等宽
    "C:/Windows/Fonts/cour.ttf",                             # Windows Courier（打字机风）
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",  # Linux Courier 风格
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",           # Linux 终端风
    "/System/Library/Fonts/Menlo.ttc",                        # macOS 等宽
]
_MONO_BOLD_CANDIDATES = [
    "assets/fonts/mono-bold.ttf",
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/courbd.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]
# 中文/日文用的 CJK 字体
_CJK_CANDIDATES = [
    "assets/fonts/cjk.ttf",                                   # 用户自放（最高优先）
    "C:/Windows/Fonts/msyh.ttc",                              # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",                            # Windows 黑体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", # Linux 思源黑体
    "/System/Library/Fonts/PingFang.ttc",                     # macOS 苹方
]


def _load_font(candidates: list, size: int):
    """按优先级尝试加载字体；全部失败则降级为默认字体，保证程序不闪退。"""
    for rel in candidates:
        path = resource_path(rel)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue                      # 单个字体坏了就试下一个
    try:
        return ImageFont.load_default(size)   # Pillow 10.1+ 支持指定字号
    except TypeError:
        return ImageFont.load_default()       # 老版本 Pillow 降级


def _load_font_pair(size: int, bold: bool = False):
    """同时加载 (等宽字体, 中文字体)，实现打印质感的中英混排。"""
    mono_list = _MONO_BOLD_CANDIDATES if bold else _MONO_CANDIDATES
    mono = _load_font(mono_list, size)
    cjk = _load_font(_CJK_CANDIDATES, size)
    return mono, cjk


# ============================================================
# 三、中英混排：测量 + 绘制 + 换行
# ============================================================
def _is_cjk(ch: str) -> bool:
    """判断字符是否属于 CJK 区段（中文/日文/韩文）。"""
    return ord(ch) > 0x2E80


def _char_width(ch: str, mono_font, cjk_font, probe) -> float:
    """单个字符的像素宽度（按混合字体规则）。"""
    f = cjk_font if _is_cjk(ch) else mono_font
    return probe.textlength(ch, font=f)


def _measure(text: str, mono_font, cjk_font, probe) -> float:
    """按混合字体规则测量一行文本的像素宽度。"""
    if mono_font is None or cjk_font is None:
        return probe.textlength(text, font=mono_font or cjk_font)
    return sum(_char_width(ch, mono_font, cjk_font, probe) for ch in text)


def _wrap_text(text: str, max_w: float, mono_font, cjk_font, probe) -> list:
    """
    把超宽的文本切成多行，每行宽度都 ≤ max_w。
    断行优先回退到「最后一个逗号或空格」（不拆散单词/语义群，日语长列举更自然）；
    没有可用断点（如连续 CJK）才逐字符硬拆。
    """
    lines = []
    cur = ""
    for ch in text:
        if cur and _measure(cur + ch, mono_font, cjk_font, probe) > max_w:
            sp = max(cur.rfind(" "), cur.rfind(","), cur.rfind("，"))
            if sp > 0:
                lines.append(cur[:sp].rstrip())
                cur = cur[sp + 1:].lstrip() + ch
            else:
                lines.append(cur)
                cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines if lines else [""]


def _draw_mixed(draw, xy, text, mono_font, cjk_font, fill, probe):
    """按混合字体规则逐字符绘制一行文本（基线对齐，避免中英混排上下错落）。"""
    x, y = xy
    if mono_font is None or cjk_font is None:
        draw.text(xy, text, font=mono_font or cjk_font, fill=fill)
        return
    # 两种字体的基线统一到同一高度：各字符顶部 = 基线 - 该字体 ascent
    base = y + max(mono_font.getmetrics()[0], cjk_font.getmetrics()[0])
    for ch in text:
        f = cjk_font if _is_cjk(ch) else mono_font
        asc = f.getmetrics()[0]
        draw.text((x, base - asc), ch, font=f, fill=fill)
        x += probe.textlength(ch, font=f)


# ============================================================
# 四、边缘蒙版（当前版本：平直直线；如需复古锯齿撕边可调用 _zigzag_mask）
# ============================================================
def _zigzag_mask(width: int, height: int, tooth_h: int = 26, tooth_w: int = 18) -> Image.Image:
    """
    生成左右边缘带三角锯齿的 alpha 蒙版（L 模式，255=不透明）。
    tooth_h: 每个锯齿的高度像素
    tooth_w: 锯齿横向咬入的深度像素（越大撕边越夸张）
    """
    mask = Image.new("L", (width, height), 255)   # 全白 = 完全不透明
    draw = ImageDraw.Draw(mask)

    y = 0
    while y < height:                             # 左边缘：一排向内咬的三角缺口
        draw.polygon([(0, y), (tooth_w, y + tooth_h // 2), (0, y + tooth_h)], fill=0)
        y += tooth_h

    y = 0
    while y < height:                             # 右边缘：镜像再来一排
        draw.polygon([(width, y), (width - tooth_w, y + tooth_h // 2), (width, y + tooth_h)], fill=0)
        y += tooth_h
    return mask


# ============================================================
# 五、褶皱纸张背景（拍照感的自然褶皱）
# ============================================================
# 模块级缓存：真实纸面纹理只加载一次
_TEXTURE_CACHE = None


def _load_texture():
    """加载整体纸面纹理（两张变体随机选，兼容 PyInstaller 临时解压目录），失败返回 None"""
    global _TEXTURE_CACHE
    if _TEXTURE_CACHE is not None:
        return _TEXTURE_CACHE
    try:
        base = resource_path("assets")
        candidates = ["paper_texture.png", "paper_texture_b.png"]
        paths = [os.path.join(base, n) for n in candidates if os.path.exists(os.path.join(base, n))]
        if paths:
            _TEXTURE_CACHE = Image.open(random.choice(paths)).convert("L")
            return _TEXTURE_CACHE
    except Exception:
        pass
    return None


def _paper_background(width: int, height: int) -> Image.Image:
    """
    生成「像照片拍的真实小票」纸张背景，优先使用整体纸面纹理：
      1. 整体纹理（参考图提取：干净纸面 + 稀疏原生折痕，一体无条带感）
         → 随机裁剪不同区域 + 翻转 + 亮度微调，每次折痕位置不同
      2. 找不到纹理时，回退到程序化生成
    最后统一叠加：极淡渍斑 + 边缘卷曲阴影 + 轻微颗粒。
    """
    rng = random.Random()                    # 不固定种子：每次渲染折痕随机出现，效果各不相同
    tex = _load_texture()
    if tex is not None and tex.size[0] >= width:
        paper = _paper_from_texture(tex, width, height, rng)
    else:
        paper = _paper_procedural(width, height, rng)
    return _paper_finish(paper, width, height, rng)


def _paper_from_texture(tex: Image.Image, width: int, height: int, rng) -> Image.Image:
    """真实照片折痕光影场：优先裁剪折痕丰富区 + 翻转 + 小角度旋转 + 折痕增强"""
    tw, th = tex.size
    src = tex
    if th < height:                          # 歌单很长时垂直缩放光影场兜底
        src = tex.resize((tw, height), Image.BICUBIC)
        th = height

    # 扫描 24 个随机窗口，按局部明暗方差挑折痕最丰富的前 8 个，再随机选一个
    # （保证每张都有可见折痕，同时位置每次不同）
    # 注意：oy 只从纹理中上部取样，确保小票顶部区域也有折痕（用户反馈顶部偏干净）
    cands = []
    oy_max = max(0, (th - height) // 2)
    for _ in range(24):
        ox = rng.randint(0, tw - width)
        oy = rng.randint(0, oy_max)
        small = src.crop((ox, oy, ox + width, oy + height)).resize((33, 55), Image.BICUBIC)
        vals = list(small.getdata())
        m = sum(vals) / len(vals)
        std = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
        cands.append((std, ox, oy))
    cands.sort(key=lambda t: t[0], reverse=True)
    _, ox, oy = rng.choice(cands[:8])
    tile = src.crop((ox, oy, ox + width, oy + height))

    if rng.random() < 0.5:                   # 一半概率左右翻转
        tile = tile.transpose(Image.FLIP_LEFT_RIGHT)
    # 小角度旋转 ±3°：折痕整体方向每次不同（角部填纸面色，边缘阴影会盖住）
    angle = rng.uniform(-3, 3)
    tile = tile.rotate(angle, resample=Image.BICUBIC, fillcolor=224)

    # 亮度微调 ±5 + 折痕对比增强 ×1.4（图一风格：柔和折痕，纸面干净浅灰）
    adj = rng.uniform(-5, 5)
    tile = tile.point(lambda v: max(200, min(238, int(224 + (v + adj - 224) * 1.4))))

    # 折痕边界柔化锐化：图一折痕是 ~100px 软渐变，只轻微增强边界，
    # 不产生锋利黑线（去"拉链感"），纸面保持平滑
    blur = tile.filter(ImageFilter.GaussianBlur(14))
    edge = ImageChops.subtract(tile, blur).point(lambda v: min(255, int(v * 5)))
    edge = edge.filter(ImageFilter.GaussianBlur(2))
    sharp = tile.filter(ImageFilter.UnsharpMask(radius=3, percent=95, threshold=3))
    tile = Image.composite(sharp, tile, edge)

    # 纸纤维漫反射肌理：柔和中频噪声（图一仅轻微肌理，压到很低）
    fib = Image.effect_noise((max(8, width // 3), max(8, height // 3)), 22)
    fib = fib.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(1.5))
    fib = fib.point(lambda v: max(0, min(255, int(224 + (v - 128) * 0.7))))
    tile = Image.blend(tile, fib, 0.07)

    g = tile
    r = g.point(lambda v: max(0, v - 2))     # R 略低 → 冷白纸面（真实照片色调）
    b = g.point(lambda v: min(255, v + 3))   # B 略高 → 冷白纸面（真实照片色调）
    return Image.merge("RGB", (r, g, b))


def _paper_procedural(width: int, height: int, rng) -> Image.Image:
    """回退路径：程序化生成纸面（正弦波卷曲光照 + 贝塞尔折痕亮度场）"""
    import math as _m

    sx, sy = max(48, width // 10), max(64, height // 10)
    lum = Image.new("RGB", (sx, sy), (128, 128, 128))
    ld = ImageDraw.Draw(lum, "RGBA")

    # 大尺度卷曲光照（正弦波：整张纸像被轻轻卷曲）
    waves = []
    for _ in range(rng.randint(2, 3)):
        ang = rng.uniform(0, 180)
        lam = rng.uniform(max(width, height) * 0.6, max(width, height) * 1.5)
        amp = rng.uniform(18, 38)
        waves.append((_m.cos(_m.radians(ang)), _m.sin(_m.radians(ang)), lam, amp))
    for y in range(sy):
        for x in range(sx):
            v = 128
            for wdx, wdy, lam, amp in waves:
                v += amp * _m.sin(2 * _m.pi * (x * 10 * wdx + y * 10 * wdy) / lam)
            ld.point((x, y), (max(0, min(255, int(v))),) * 3)

    # 纸面肌理噪声（克制用量避免"磨砂感"）
    n1 = Image.effect_noise((sx, sy), 70).convert("RGB")
    lum = Image.blend(lum, n1, 0.12)
    n2 = Image.effect_noise((sx, sy), 35).convert("RGB")
    lum = Image.blend(lum, n2, 0.08)

    # 折痕：贝塞尔弯曲 + 高斯明暗带（画进亮度场，与肌理一体）
    def _band_l(p1, p2, nx, ny, half_w, peak, dark):
        """在亮度场上画高斯明暗带（小尺寸坐标，柔和渐变）"""
        for i in range(1, half_w * 2):
            off = i - half_w
            t = abs(off) / half_w
            a = int(peak * _m.exp(-t * t * 2.0))
            if a <= 2:
                continue
            ox, oy = nx * off, ny * off
            fill = (58, 58, 58, a) if dark else (212, 212, 212, a)
            ld.line([(p1[0] + ox, p1[1] + oy), (p2[0] + ox, p2[1] + oy)], fill=fill, width=2)

    def _bezier_l(p0, c1, c2, p1, n=40):
        """三次贝塞尔曲线采样点列（小尺寸坐标，折痕 S 形弯曲）"""
        pts = []
        for t in range(n + 1):
            u = t / n
            x = (1 - u) ** 3 * p0[0] + 3 * (1 - u) ** 2 * u * c1[0] + 3 * (1 - u) * u * u * c2[0] + u ** 3 * p1[0]
            y = (1 - u) ** 3 * p0[1] + 3 * (1 - u) ** 2 * u * c1[1] + 3 * (1 - u) * u * u * c2[1] + u ** 3 * p1[1]
            pts.append((x, y))
        return pts

    def _crease_l(theta, length, half_w, peak_dark, peak_light):
        """在亮度场上画一条弯曲折痕（小尺寸坐标）"""
        x0 = rng.uniform(4, sx - 4)
        y0 = rng.uniform(4, sy - 4)
        dx, dy = _m.cos(_m.radians(theta)), _m.sin(_m.radians(theta))
        span = length * max(sx, sy) * rng.uniform(0.6, 1.1)
        x2 = x0 + dx * span + rng.uniform(-8, 8)
        y2 = y0 + dy * span + rng.uniform(-8, 8)
        mx, my = (x0 + x2) / 2, (y0 + y2) / 2
        bend = rng.uniform(5, 15)
        c1 = (mx - dy * bend + rng.uniform(-4, 4), my + dx * bend + rng.uniform(-4, 4))
        c2 = (mx + dy * bend * 0.6 + rng.uniform(-4, 4), my - dx * bend * 0.6 + rng.uniform(-4, 4))
        pts = _bezier_l((x0, y0), c1, c2, (x2, y2))
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            ang = _m.atan2(b[1] - a[1], b[0] - a[0])
            nx, ny = -_m.sin(ang), _m.cos(ang)
            _band_l(a, b, nx, ny, half_w, peak_dark, True)   # 暗谷（折痕凹陷）
            _band_l(a, b, nx, ny, half_w, peak_light, False)  # 亮脊（折痕反光）

    # 大折痕 1~2 条 / 中折痕 1~3 条 / 细皱纹 3~6 条
    for _ in range(rng.randint(1, 2)):
        _crease_l(rng.uniform(0, 180), rng.uniform(0.8, 1.0),
                  rng.randint(3, 5), rng.randint(105, 140), rng.randint(60, 85))
    for _ in range(rng.randint(1, 3)):
        _crease_l(rng.uniform(0, 180), rng.uniform(0.45, 0.7),
                  rng.randint(2, 3), rng.randint(65, 90), rng.randint(40, 60))
    for _ in range(rng.randint(3, 6)):
        _crease_l(rng.uniform(0, 180), rng.uniform(0.15, 0.35),
                  rng.randint(1, 2), rng.randint(45, 70), rng.randint(28, 45))

    # 对折分界：极宽极淡的整体明暗过渡
    theta = rng.uniform(0, 180)
    x0 = rng.uniform(sx * 0.15, sx * 0.85)
    y0 = rng.uniform(sy * 0.15, sy * 0.85)
    dx, dy = _m.cos(_m.radians(theta)), _m.sin(_m.radians(theta))
    x1 = x0 + dx * max(sx, sy)
    y1 = y0 + dy * max(sx, sy)
    nx, ny = -dy, dx
    _band_l((x0, y0), (x1, y1), nx, ny, 14, 12, True)
    _band_l((x0, y0), (x1, y1), nx, ny, 14, 9, False)

    # 放大 + 微模糊融合 + 纸色着色（纸面亮到 220，接近参考图）
    lum_big = lum.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(1.2))
    g = lum_big.convert("L").point(lambda v: max(0, min(255, 220 + (v - 128) * 0.45)))
    r = g.point(lambda v: min(255, v + 6))     # R 略高 → 轻微暖米色
    b = g.point(lambda v: max(0, v - 8))       # B 略低 → 轻微暖米色
    return Image.merge("RGB", (r, g, b))


def _paper_finish(paper: Image.Image, width: int, height: int, rng) -> Image.Image:
    """公共后处理：极淡旧纸渍斑 + 边缘卷曲阴影 + 轻微颗粒"""
    # 旧纸渍斑（几乎不可见的淡黄，2 处，保持纸面干净）
    stain = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stain)
    for _ in range(2):
        sxx = rng.randint(0, width)
        syy = rng.randint(0, height)
        sr = rng.randint(60, 120)
        sd.ellipse([sxx - sr, syy - sr, sxx + sr, syy + sr], fill=(205, 188, 148, 8))
    paper = Image.alpha_composite(paper.convert("RGBA"), stain).convert("RGB")

    # 边缘 + 上下卷曲阴影（纸张厚度与卷曲投影；上下很轻，避免"黑边"）
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sh = ImageDraw.Draw(shadow)
    grad_px = 70
    for i in range(grad_px):
        t = i / grad_px
        a = int(60 * (1 - t) ** 1.5)
        sh.line([(i, 0), (i, height)], fill=(52, 48, 42, a))                    # 左
        sh.line([(width - 1 - i, 0), (width - 1 - i, height)], fill=(52, 48, 42, a))  # 右
    top_h = 30
    for i in range(top_h):
        t = i / top_h
        a = int(14 * (1 - t) ** 1.5)              # 上下阴影极轻（去黑边）
        sh.line([(0, i), (width, i)], fill=(52, 48, 42, a))                      # 上
        sh.line([(0, height - 1 - i), (width, height - 1 - i)], fill=(52, 48, 42, a))  # 下
    paper = Image.alpha_composite(paper.convert("RGBA"), shadow).convert("RGB")

    # 热敏纸涂层颗粒：中频细腻颗粒（模拟纸面涂层质感，均匀不脏）
    coat = Image.effect_noise((max(8, width // 6), max(10, height // 6)), 45)
    coat = coat.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    coat = coat.point(lambda v: 128 + (v - 127) * 0.45)
    paper = Image.blend(paper, Image.merge("RGB", (coat, coat, coat)), 0.03)

    # 轻微颗粒（真实纹理自带肌理，只加一点点）
    grain = Image.effect_noise((width, height), 9).convert("L")
    grain = grain.point(lambda v: 128 + (v - 127) * 0.4)
    return Image.blend(paper, Image.merge("RGB", (grain, grain, grain)), 0.015)


# ============================================================
# 六、行解析：把一行文本翻译成 (样式, 内容)
# ============================================================
def _parse_line(line: str):
    """返回 (style, data)。style 决定画法，data 是绘制所需数据。"""
    ln = line.rstrip()
    if not ln.strip():
        return ("blank", None)
    if ln.startswith(";;"):
        return ("comment", None)                    # 注释
    if ln.startswith("@"):
        return ("brand", ln[1:].strip())            # 底部平台名（如 @Spotify）
    if ln.startswith("###"):
        return ("center_small", ln[3:].strip())     # 居中灰色小字
    if ln.startswith("##"):
        return ("center_sub", ln[2:].strip())       # 居中副标题
    if ln.startswith("#"):
        return ("center_title", ln[1:].strip())     # 居中大标题
    s = ln.strip()
    if len(set(s)) == 1 and s[0] in "-=~·":
        return ("divider", None)                    # 虚线分隔线
    if "|" in ln:
        parts = [p.strip() for p in ln.split("|")]
        return ("row", parts)                       # 表格行 / 信息行
    return ("plain", ln)                            # 普通左对齐


# ============================================================
# 七、核心渲染：纯文本 → 固定宽度的复古小票图片
# ============================================================
def render_receipt(text: str) -> Image.Image:
    """
    把蓄水池里的纯文本渲染成一张复古小票（返回 RGBA 图片）。
    纸张宽度固定为 FIXED_PAPER_W，内容超宽自动换行。
    """
    # ---------- 第一步：逐行解析 ----------
    parsed = []
    for ln in text.splitlines():
        style, data = _parse_line(ln)
        if style not in ("blank", "comment"):
            parsed.append((style, data))

    # ---------- 排版参数（宽度固定） ----------
    margin = 56                # 左右留白
    line_h = 42                # 每个物理行的行距
    title_h = 58               # 大标题行距
    font_size = 22             # 正文字号
    small_size = 20            # 灰色小字号
    col1_w = 92                # 第一列宽（序号/键）
    col2_x = margin + col1_w   # 第二列起点（歌名/内容）

    canvas_w = FIXED_PAPER_W   # 固定纸张宽度（不再自适应）
    canvas_w = canvas_w // 2 * 2   # 保持偶数，锯齿左右对称
    col3_x = canvas_w - margin  # 第三列右对齐锚点
    max_item_w = col3_x - col2_x - 30      # 歌名/内容列可用宽（留 30 防撞时长）
    max_info_w = canvas_w - margin * 2     # 信息行/普通行的可用宽

    # ---------- 双字体 ----------
    font_m, font_c = _load_font_pair(font_size)
    small_m, small_c = _load_font_pair(small_size)
    title_m, title_c = _load_font_pair(font_size + 12, bold=True)
    sub_m, sub_c = _load_font_pair(font_size + 2)

    # ---------- 第二步：预处理换行，生成物理行 ----------
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    prepared = []            # 每个元素: (style, lines, extra)
    for style, data in parsed:
        if style == "row":
            if len(data) == 3:
                num, item, amt = data
                item_lines = _wrap_text(item, max_item_w, font_m, font_c, probe)
                prepared.append(("row3", item_lines, (num, amt)))
            elif len(data) == 2:
                key, val = data
                key_w = _measure(key, font_m, font_c, probe)
                val_w = _measure(val, font_m, font_c, probe)
                if key_w + val_w <= max_info_w:
                    prepared.append(("row2", [val], (key,)))        # 键值同行
                else:
                    val_lines = _wrap_text(val, max_info_w, font_m, font_c, probe)
                    prepared.append(("row2v", val_lines, (key,)))   # 键一行，值换行
            else:
                lines = _wrap_text(data[0], max_item_w, font_m, font_c, probe)
                prepared.append(("row1", lines, None))
        elif style == "plain":
            lines = _wrap_text(data, max_info_w, font_m, font_c, probe)
            prepared.append(("plain", lines, None))
        elif style == "brand":
            lines = _wrap_text(data, max_info_w, sub_m, sub_c, probe)
            prepared.append(("brand", lines, None))
        elif style == "center_title":
            lines = _wrap_text(data, max_info_w, title_m, title_c, probe)
            prepared.append(("title", lines, None))
        elif style == "center_sub":
            lines = _wrap_text(data, max_info_w, sub_m, sub_c, probe)
            prepared.append(("sub", lines, None))
        elif style == "center_small":
            lines = _wrap_text(data, max_info_w, small_m, small_c, probe)
            prepared.append(("small", lines, None))
        else:  # divider
            prepared.append(("divider", [""], None))

    # ---------- 第三步：估算总高度 ----------
    total_h = margin
    for style, lines, extra in prepared:
        if style == "title":
            total_h += title_h + 4
        elif style == "row2v":
            total_h += line_h * (1 + len(lines))   # 键一行 + 值若干行
        else:
            total_h += line_h * max(1, len(lines))
    total_h += 130                              # 底部条码区预留

    # ---------- 第四步：画背景 + 逐物理行绘制 ----------
    img = _paper_background(canvas_w, total_h)
    draw = ImageDraw.Draw(img)

    y = margin
    for style, lines, extra in prepared:
        if style == "title":
            for ln in lines:
                w = _measure(ln, title_m, title_c, probe)
                _draw_mixed(draw, ((canvas_w - w) // 2, y + 6), ln, title_m, title_c, "black", probe)
                y += title_h
            y += 4
        elif style == "sub":
            for ln in lines:
                w = _measure(ln, sub_m, sub_c, probe)
                _draw_mixed(draw, ((canvas_w - w) // 2, y + 8), ln, sub_m, sub_c, "black", probe)
                y += line_h
        elif style == "small":
            for ln in lines:
                w = _measure(ln, small_m, small_c, probe)
                _draw_mixed(draw, ((canvas_w - w) // 2, y + 12), ln, small_m, small_c, (88, 88, 88), probe)
                y += line_h
        elif style == "divider":
            x = margin
            while x < canvas_w - margin:
                draw.rectangle([x, y + line_h // 2 - 2, x + 14, y + line_h // 2 + 2], fill=(120, 118, 114))
                x += 26
            y += line_h
        elif style == "row3":
            num, amt = extra
            last = len(lines) - 1
            for i, ln in enumerate(lines):
                if i == 0 and num:
                    _draw_mixed(draw, (margin, y + 8), num, font_m, font_c, "black", probe)
                # 时长跟随「最后一行」：歌名换行时与末行对齐，不与下行/时间重叠
                if i == last and amt:
                    w = _measure(amt, font_m, font_c, probe)
                    _draw_mixed(draw, (col3_x - w, y + 8), amt, font_m, font_c, "black", probe)
                if ln:
                    _draw_mixed(draw, (col2_x, y + 8), ln, font_m, font_c, "black", probe)
                y += line_h
        elif style == "row2":
            key, = extra
            _draw_mixed(draw, (margin, y + 8), key, font_m, font_c, "black", probe)
            val = lines[0]
            w = _measure(val, font_m, font_c, probe)
            _draw_mixed(draw, (col3_x - w, y + 8), val, font_m, font_c, "black", probe)
            y += line_h
        elif style == "row2v":
            key, = extra
            _draw_mixed(draw, (margin, y + 8), key, font_m, font_c, "black", probe)
            y += line_h
            for ln in lines:
                w = _measure(ln, font_m, font_c, probe)
                _draw_mixed(draw, (col3_x - w, y + 8), ln, font_m, font_c, "black", probe)
                y += line_h
        elif style == "brand":
            # 底部平台名：居中、稍大、深色（由文本区的 @行 控制）
            for ln in lines:
                w = _measure(ln, sub_m, sub_c, probe)
                _draw_mixed(draw, ((canvas_w - w) // 2, y + 8), ln, sub_m, sub_c, (40, 40, 40), probe)
                y += line_h
        else:  # row1 / plain
            for ln in lines:
                _draw_mixed(draw, (col2_x if style == "row1" else margin, y + 8),
                            ln, font_m, font_c, "black", probe)
                y += line_h

    # ---------- 底部条码（失败不影响整张小票） ----------
    try:
        # 条码内容用短随机号（6 位），避免长字符串生成又长又密的"黑边"效果
        code_text = f"RCP-{int(time.time()) % 1000000:06d}"
        code = barcode.get("code128", code_text, writer=ImageWriter())
        # 注意：python-barcode 0.16+ 的参数名是 writer_options（旧版叫 options）
        code_img = code.render(writer_options={
            "write_text": False,
            "module_width": 0.22,
            "module_height": 6,
        })
        bar_w, bar_h = code_img.size
        max_w = canvas_w - margin * 2
        if bar_w > max_w:                        # 超宽则等比缩小
            scale = max_w / bar_w
            code_img = code_img.resize((int(bar_w * scale), int(bar_h * scale)))
        img.paste(code_img, ((canvas_w - code_img.size[0]) // 2, y + 10))
        y += code_img.size[1] + 10
    except Exception:
        pass

    # ---------- 裁剪到实际内容高度 ----------
    # 注意：crop 高度用 min(y+margin, total_h) 防止越界——PIL crop 越界会把
    # 超出部分填充为黑色（就是之前小票底部"长方形黑边"的来源）
    img = img.crop((0, 0, canvas_w, min(y + margin, total_h)))

    # ---------- 输出 RGBA：边缘为平直直线（不再应用锯齿撕边蒙版） ----------
    w, h = img.size
    rgba = img.convert("RGBA")
    rgba.putalpha(Image.new("L", (w, h), 255))   # 全不透明 = 平直边缘
    return rgba


def save_receipt(text: str, path: str) -> str:
    """渲染并保存到指定路径，返回保存路径。"""
    render_receipt(text).save(path)
    return path


# ============================================================
# 八、命令行自测入口：python receipt_render.py
# ============================================================
if __name__ == "__main__":
    demo = """;; 这是模板示例：;; 开头是注释，不会渲染
#RECEIPTIFY
##LAST 6 MONTHS
###ORDER #0002 FOR NANCY
###THURSDAY, AUGUST 27, 2026
---
QTY|ITEM|AMT
01|神様のいうとおり - JUNJI|3:38
|ISHIWATARI, 砂原良德, YAKUSHIMARU ETSUKO|
02|IF I - TREASURE|3:00
03|ばらの花 ~REMIXED BY REI HARAKAMI - 團團轉樂團|6:19
04|ばらの花 - 團團轉樂團|5:01
05|COME AGAIN - M-FLO|5:52
06|ZOOM ZOOM - TREASURE|2:58
07|OBSESSED - AYUMU IMAZU|3:17
08|どっかの土曜日 - 鈴木真海子|3:34
09|春泥棒 - ヨルシカ|4:50
10|差し色 - BIALYSTOCKS|3:05
---
SONGS|10
PLAYTIME|41:34
---
USER|NANCY
---
#THANK YOU FOR VISITING!
###receiptify.herokuapp.com
@Spotify
"""
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "demo_receipt.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    save_receipt(demo, out)
    print(f"自测完成：{out}")
