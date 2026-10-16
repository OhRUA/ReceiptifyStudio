"""
main.py — Receiptify Studio 主程序（UI 层 / 中转蓄水池模式）

数据流（高容错设计）：
    net.py 抓取 ──纯文本──▶ CTkTextbox（蓄水池，可手动编辑）
                                │ 点击「生成小票 PNG」→ 弹出保存对话框选路径
                                ▼
    receipt_render.py ◀──纯文本── 渲染成复古小票长图，保存到用户选的位置

防坑三原则：
1. 所有网络请求 / 耗时渲染都放 threading.Thread 子线程，
   结果通过 self.after() 回主线程更新 UI，杜绝界面卡死。
2. 文本区是唯一数据中转站：API 挂了，用户照样手动打字出图。
3. 保存路径由用户每次选择（保存对话框），不写死在临时目录。
"""

import os
import threading
from datetime import datetime
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image as PILImage

import net
import receipt_render

# 蓄水池预填模板：语法说明见 receipt_render.py 头部注释
_TEMPLATE = """;; 复古小票模板（;; 开头是注释，不会渲染到图里）
;; # 居中大标题   ## 居中副标题   ### 居中灰色小字
;; @平台名                ← 底部显示的平台（@NetEasyMusic、@QQMusic、@Spotify、@AppleMusic、@iTunes…）
;; 序号|歌名 - 歌手|时长   ← 表格行（时长右对齐，超长歌名自动换行）
;; 键名|数值               ← 信息行（ITEM COUNT/TOTAL/CARD #/AUTH CODE 等）
;; 从各平台链接导入会自动生成这些行（可全部手动修改）
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
ITEM COUNT|10
TOTAL|41:34
---
CARD #|**** **** **** 2023
AUTH CODE|123421
CARDHOLDER|NANCY
---
#THANK YOU FOR VISITING!
###receiptify.studio
@NetEasyMusic
"""


class ReceiptifyApp(ctk.CTk):
    """主窗口：输入区 + 蓄水池文本区 + 预览区
    UI 风格借鉴 open-claude-ui 的 Claude 设计系统令牌：
      陶土橙 Clay #d97757（主操作）、Surface 阶梯 #f5f5f3/#ffffff、
      8px 圆角、发丝细边 #e5e5e0。"""

    # ---- Claude 设计令牌（集中定义，改主题只动这里） ----
    CLAY = "#d97757"            # 陶土橙（主按钮/焦点）
    CLAY_HOVER = "#c15f3a"
    SURFACE_0 = "#f5f5f3"       # 窗口底色（最低层）
    SURFACE_1 = "#ffffff"       # 输入区/文本区（白色纸感）
    SURFACE_2 = "#ecece9"       # 次级按钮
    SURFACE_2_HOVER = "#dededb"
    HAIRLINE = "#e5e5e0"        # 发丝细边
    INK = "#1a1a19"             # 主文字
    INK_2 = "#6b6b67"           # 次级文字

    def __init__(self):
        super().__init__()
        self.title("Receiptify Studio — 复古小票歌单生成器")
        self.geometry("720x820")
        ctk.set_appearance_mode("light")   # 复古小票场景用浅色主题更搭
        self.configure(fg_color=self.SURFACE_0)

        # ---------- 顶部：输入框 + 抓取按钮 ----------
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))

        self.entry = ctk.CTkEntry(
            top,
            placeholder_text="粘贴网易云/QQ音乐/Spotify/Apple Music 歌单链接，或输入 iTunes 搜索关键词…",
            corner_radius=8, border_color=self.HAIRLINE, fg_color=self.SURFACE_1,
        )
        self.entry.pack(side="left", fill="x", expand=True)

        self.btn_netease = ctk.CTkButton(
            top, text="导入歌单", width=100, corner_radius=8,
            fg_color=self.SURFACE_2, hover_color=self.SURFACE_2_HOVER,
            text_color=self.INK, command=self._on_import_netease)
        self.btn_netease.pack(side="left", padx=(8, 0))

        self.btn_itunes = ctk.CTkButton(
            top, text="搜索 iTunes", width=100, corner_radius=8,
            fg_color=self.SURFACE_2, hover_color=self.SURFACE_2_HOVER,
            text_color=self.INK, command=self._on_search_itunes)
        self.btn_itunes.pack(side="left", padx=(8, 0))

        self.btn_clear = ctk.CTkButton(
            top, text="清空", width=60, corner_radius=8,
            fg_color="transparent", hover_color=self.SURFACE_2,
            text_color=self.INK_2, command=self._on_clear)
        self.btn_clear.pack(side="left", padx=(8, 0))

        # ---------- 中部：蓄水池文本区（数据唯一中转站） ----------
        self.textbox = ctk.CTkTextbox(
            self, wrap="word", corner_radius=10,
            border_color=self.HAIRLINE, fg_color=self.SURFACE_1,
            text_color=self.INK,
            # TkFixedFont 是 Tk 内建的等宽字体别名，跨平台自动映射，避免指定字体缺失
            font=ctk.CTkFont(family="TkFixedFont", size=14),
        )
        self.textbox.pack(fill="both", expand=True, padx=16, pady=8)
        self.textbox.insert("1.0", _TEMPLATE)

        # ---------- 底部：状态栏 + 生成按钮 ----------
        bottom = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(8, 16))

        self.status = ctk.CTkLabel(bottom, text="就绪。", anchor="w", text_color=self.INK_2)
        self.status.pack(side="left", fill="x", expand=True)

        self.btn_generate = ctk.CTkButton(
            bottom, text="生成小票 PNG", corner_radius=8,
            fg_color=self.CLAY, hover_color=self.CLAY_HOVER, text_color="#ffffff",
            font=ctk.CTkFont(weight="bold"), command=self._on_generate)
        self.btn_generate.pack(side="right")

        # ---------- 预览区：生成后展示小票效果 ----------
        self.preview_label = ctk.CTkLabel(
            self, text="生成的小票预览会显示在这里", text_color=self.INK_2,
        )
        self.preview_label.pack(fill="x", padx=16, pady=(0, 16))
        self._preview_img = None   # 防止 CTkImage 被垃圾回收

    # ==================== 网络抓取（全部走子线程） ====================

    def _on_import_netease(self):
        """导入歌单：校验输入 → 子线程抓取 → after 回主线程写文本区
        支持网易云 / QQ音乐 / Spotify / Apple Music 的歌单或单曲链接。"""
        url = self.entry.get().strip()
        if not url:
            self.status.configure(text="请先粘贴一个歌单链接（网易云/QQ音乐/Spotify/Apple Music）。")
            return
        self._set_busy(True, "正在抓取歌单，请稍候…")
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url):
        """子线程里跑网络请求，绝不碰 UI；结果通过 after 交回主线程。
        统一走 fetch_music：按域名自动识别平台。"""
        text = net.fetch_music(url)
        self.after(0, lambda: self._finish_fetch(text))

    def _finish_fetch(self, text):
        self._set_busy(False)
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", text)
        self.status.configure(text="已写入文本区。可直接编辑，再点「生成小票 PNG」。")

    def _on_search_itunes(self):
        term = self.entry.get().strip()
        if not term:
            self.status.configure(text="请先在输入框填一个搜索关键词。")
            return
        self._set_busy(True, "正在搜索 iTunes，请稍候…")
        threading.Thread(target=self._itunes_worker, args=(term,), daemon=True).start()

    def _itunes_worker(self, term):
        text = net.search_itunes(term)
        self.after(0, lambda: self._finish_fetch(text))

    # ==================== 生成小票（渲染也走子线程） ====================

    def _on_generate(self):
        text = self.textbox.get("1.0", "end").strip()
        if not text:
            self.status.configure(text="文本区是空的，先输入歌单内容吧。")
            return
        # 弹出「另存为」对话框，让用户自己选保存位置（对话框必须在主线程弹）
        # 默认目录：我的文档；默认文件名：receipt_年月日_时分秒.png
        default_dir = os.path.join(os.path.expanduser("~"), "Documents")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")
        path = filedialog.asksaveasfilename(
            title="保存小票图片",
            initialdir=default_dir,
            initialfile=f"receipt_{datetime.now():%Y%m%d_%H%M%S}.png",
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")],
        )
        if not path:  # 用户点了取消，什么都不做
            self.status.configure(text="已取消保存。")
            return
        self._set_busy(True, "正在渲染复古小票…")
        threading.Thread(target=self._render_worker, args=(text, path), daemon=True).start()

    def _render_worker(self, text, path):
        """子线程渲染图片到用户选的路径，只在本线程做文件 IO；UI 更新交给 after"""
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            receipt_render.save_receipt(text, path)
            self.after(0, lambda: self._finish_render(path))
        except Exception as e:
            self.after(0, lambda: self._set_busy(False) or self.status.configure(
                text=f"生成失败：{e}"))

    def _finish_render(self, path):
        """渲染完成：加载图片 → 缩放到预览区 → 更新状态"""
        self._set_busy(False)
        try:
            img = PILImage.open(path)
            img.thumbnail((680, 400))
            self._preview_img = ctk.CTkImage(light_image=img, size=img.size)
            self.preview_label.configure(image=self._preview_img, text="")
        except Exception:
            self.preview_label.configure(text="（预览加载失败，但文件已保存）")
        self.status.configure(text=f"已生成：{path}")

    # ==================== 小工具 ====================

    def _on_clear(self):
        self.textbox.delete("1.0", "end")
        self.status.configure(text="已清空。")

    def _set_busy(self, busy: bool, msg: str = ""):
        """统一切换忙/闲状态：忙碌时禁用按钮，防止重复点击"""
        state = "disabled" if busy else "normal"
        for b in (self.btn_netease, self.btn_itunes, self.btn_generate):
            b.configure(state=state)
        if msg:
            self.status.configure(text=msg)


if __name__ == "__main__":
    app = ReceiptifyApp()
    app.mainloop()
