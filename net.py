"""
net.py — 网络抓取层（中转蓄水池模式的上游）

职责：把「网易云链接 / iTunes 关键词」抓成「小票格式纯文本」，
      直接写进 UI 的 CTkTextbox（蓄水池），用户可编辑后再渲染。

导入结果自动包含（与渲染引擎语法配套，字段全部随导入数据变化）：
  @平台名          → 底部平台（@NetEasyMusic、@QQMusic、@Spotify、@AppleMusic、@iTunes）
  #RECEIPTIFY      → 大标题
  ##副标题         → 歌单名 / 搜索词 / 歌名
  ###ORDER/日期    → 订单行 + 当天日期（英文格式）
  QTY|ITEM|AMT     → 表头
  序号|歌名 - 歌手|时长 → 表格行（时长右对齐，自动换算 mm:ss）
  ITEM COUNT/TOTAL → 真实统计（数量与总时长）
  CARD #/AUTH CODE/CARDHOLDER → 随导入数据生成（歌单ID/昵称等）
  ###receiptify.studio → 自家品牌网址

铁律：
1. 这里永远不直接接触图像渲染层，只输出文本。
2. 任何失败都返回可读的错误提示文本，绝不抛出异常闪退。
"""

import json
import os
import re
import zlib
from datetime import datetime

import requests

# 统一请求头：模拟浏览器，避免部分接口拒绝无 UA 的请求
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}
# QQ 音乐接口对 Referer 敏感，必须用 y.qq.com 域名
_QQ_HEADERS = {**_HEADERS, "Referer": "https://y.qq.com/"}


def _system_proxies():
    """读取系统代理（环境变量 > Windows 注册表），有则返回代理字典，没有返回 None。
    Spotify 等部分国外平台需要代理才能访问。"""
    p = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
         or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    if p:
        return {"http": p, "https": p}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
        if enabled and server:
            if not server.startswith("http://") and not server.startswith("https://"):
                server = "http://" + server
            return {"http": server, "https": server}
    except Exception:
        pass
    return None


def _fmt_ms(ms) -> str:
    """毫秒 → m:ss 格式；超过 1 小时自动变 h:mm:ss（如 214000 → 3:34）。"""
    sec = max(0, int(ms / 1000))
    if sec >= 3600:
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60}:{sec % 60:02d}"


def _today() -> str:
    """当天日期（英文格式，和小票模板一致）"""
    return datetime.now().strftime("%A, %B %d, %Y")


def _auth_code(pid: str) -> str:
    """把平台的字母长 ID（如 Spotify/Apple 的 22 位/pl. 开头串）转成 6 位数字指纹。
    同一歌单永远得到同一个码、不同歌单不同码，避免在票面上显示一长串"乱码"。"""
    return f"{zlib.crc32(pid.encode('utf-8')) % 1000000:06d}"


def _receipt_header(platform: str, title: str, user: str) -> str:
    """小票头部公共行：大标题/副标题/订单/日期/表头"""
    return (
        f"@{platform}\n"
        "#RECEIPTIFY\n"
        f"##{title}\n"
        f"###ORDER #0001 FOR {user}\n"
        f"###{_today()}\n"
        "---\n"
        "QTY|ITEM|AMT"
    )


def _receipt_footer(count, total, card: str, auth: str, holder: str) -> str:
    """小票尾部公共行：统计/卡信息/感谢语/自家网址"""
    return (
        "---\n"
        f"ITEM COUNT|{count}\n"
        f"TOTAL|{total}\n"
        "---\n"
        f"CARD #|{card}\n"
        f"AUTH CODE|{auth}\n"
        f"CARDHOLDER|{holder}\n"
        "---\n"
        "#THANK YOU FOR VISITING!\n"
        "###receiptify.studio"
    )


def _extract_song_id(url: str):
    """从链接字符串里抠出网易云歌曲 ID，找不到返回 None。"""
    m = re.search(r"song\?id=(\d+)", url) or re.search(r"/song/(\d+)", url)
    return m.group(1) if m else None


def fetch_netease_song(url: str) -> str:
    """
    网易云歌曲链接 → 小票格式纯文本（完整版式）。
    支持常见链接格式与 163cn.tv 短链。
    字段随导入数据变化：CARDHOLDER=歌手、AUTH CODE=歌曲ID。
    """
    try:
        song_id = _extract_song_id(url)
        if song_id is None and ("music.163.com" in url or "163cn.tv" in url):
            resp = requests.get(url, headers=_HEADERS, timeout=8)
            song_id = _extract_song_id(resp.url)
        if song_id is None:
            return (
                "错误：链接里找不到歌曲 ID（需要 id=数字 这一段）。\n"
                "请复制浏览器地址栏的完整链接，例如：\n"
                "https://music.163.com/song?id=1363948882"
            )

        resp = requests.get(
            f"https://music.163.com/api/song/detail?id={song_id}&ids=[{song_id}]",
            headers=_HEADERS,
            timeout=8,
        )
        data = resp.json()
        song = data["songs"][0]
        artist = song["artists"][0]["name"]
        name = song["name"]
        playtime = _fmt_ms(song.get("duration") or 0)

        lines = [
            _receipt_header("NetEasyMusic", name, artist),
            f"01|{artist} - {name}|{playtime}",
        ]
        lines.append(_receipt_footer(
            1, playtime,
            artist,        # CARD # → 歌手名（这首单曲的"主人"）
            song_id,       # AUTH CODE → 歌曲 ID（跟歌曲直接相关）
            name,          # CARDHOLDER → 歌曲名（跟歌曲直接相关）
        ))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：抓取失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def search_itunes(term: str, limit: int = 8) -> str:
    """
    iTunes Search 公开 API → 小票格式纯文本（完整版式，真实时长统计）。
    CARDHOLDER=搜索词，AUTH CODE=结果数量。
    """
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": term, "entity": "song", "limit": limit},
            headers={"User-Agent": _HEADERS["User-Agent"]},
            timeout=8,
        )
        results = resp.json().get("results", [])
        if not results:
            return "错误：没有搜到相关结果，换个关键词试试。"

        lines = [_receipt_header("iTunes", f"SEARCH: {term}", term)]
        total_ms = 0
        for i, r in enumerate(results, start=1):
            dur_ms = r.get("trackTimeMillis") or 0
            total_ms += dur_ms
            lines.append(f"{i:02d}|{r['artistName']} - {r['trackName']}|{_fmt_ms(dur_ms)}")
        lines.append(_receipt_footer(
            len(results), _fmt_ms(total_ms),
            term,                  # CARD # → 搜索关键词（iTunes 无用户概念）
            f"{len(results):04d}",  # AUTH CODE → 结果数量（跟本次搜索相关）
            f"ITUNES:{term}",      # CARDHOLDER → 搜索场景
        ))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：搜索失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def _extract_playlist_id(url: str):
    """从链接字符串里抠出网易云歌单 ID，找不到返回 None。"""
    m = re.search(r"playlist\?id=(\d+)", url) or re.search(r"/playlist/(\d+)", url)
    return m.group(1) if m else None


def fetch_netease_playlist(url: str, limit: int = 40) -> str:
    """
    网易云歌单链接 → 小票格式纯文本（完整版式）。
    CARDHOLDER=歌单创建者昵称、AUTH CODE=歌单ID、CARD #=歌单ID末4位。
    默认最多取前 40 首，避免小票过长；超出的数量会在文本里注明。
    """
    try:
        pid = _extract_playlist_id(url)
        if pid is None and ("music.163.com" in url or "163cn.tv" in url):
            resp = requests.get(url, headers=_HEADERS, timeout=8)
            pid = _extract_playlist_id(resp.url)
        if pid is None:
            return (
                "错误：链接里找不到歌单 ID（需要 playlist?id=数字 这一段）。\n"
                "请复制完整的歌单链接，例如：\n"
                "https://music.163.com/playlist?id=10045341103"
            )

        resp = requests.get(
            f"https://music.163.com/api/playlist/detail?id={pid}",
            headers=_HEADERS,
            timeout=8,
        )
        result = resp.json().get("result") or {}
        tracks = result.get("tracks") or []
        if not tracks:
            return "错误：歌单是空的，或接口暂时无响应。请稍后重试。"

        playlist_name = (result.get("name") or "").strip() or "我的歌单"
        creator = (result.get("creator") or {}).get("nickname") or "GUEST"

        lines = [_receipt_header("NetEasyMusic", playlist_name, creator)]
        shown = tracks[:limit]
        total_ms = 0
        for i, s in enumerate(shown, start=1):
            artists = ", ".join(
                a.get("name", "") for a in (s.get("artists") or s.get("ar") or [])
            )
            dur_ms = s.get("duration") or 0
            total_ms += dur_ms
            lines.append(f"{i:02d}|{artists} - {s.get('name', '')}|{_fmt_ms(dur_ms)}")
        lines.append(_receipt_footer(
            len(shown), _fmt_ms(total_ms),
            creator,            # CARD # → 歌单创建者用户名（网易云昵称）
            pid,                # AUTH CODE → 歌单 ID（跟歌单直接相关）
            creator,            # CARDHOLDER → 歌单创建者用户名（持卡人=用户）
        ))
        if len(tracks) > limit:
            lines.append(f"歌单共 {len(tracks)} 首，仅显示前 {limit} 首（可自行删改）")
        return "\n".join(lines)
    except Exception as e:
        return f"错误：歌单抓取失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def fetch_netease(url: str) -> str:
    """
    网易云链接统一入口：自动判断是「单曲」还是「歌单」，分别处理。
    main.py 的「导入歌单」按钮只需调用这一个函数。
    """
    if "playlist" in url:
        return fetch_netease_playlist(url)
    return fetch_netease_song(url)


# ============================================================
# QQ音乐 / Spotify / Apple Music 歌单抓取（同样只输出文本）
# ============================================================
def _extract_qqmusic_id(url: str):
    """从链接里抠 QQ 音乐歌单 ID（playlist/数字 或 id=数字）。"""
    m = re.search(r"playlist/(\d+)", url) or re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def fetch_qqmusic_playlist(url: str, limit: int = 40) -> str:
    """
    QQ 音乐歌单链接 → 小票格式纯文本。
    接口：c.y.qq.com 老 JSONP 接口（匿名可用，实测有效）。
    CARD #/CARDHOLDER=创建者昵称、AUTH CODE=歌单ID。
    """
    try:
        pid = _extract_qqmusic_id(url)
        if pid is None:
            return (
                "错误：链接里找不到 QQ 音乐歌单 ID。\n"
                "请复制类似 y.qq.com/n/ryqq/playlist/1755861738 的链接。"
            )
        resp = requests.get(
            "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg",
            params={"type": 1, "json": 1, "utf8": 1, "onlysong": 0,
                    "disstid": pid, "format": "json", "g_tk": "1919387379"},
            headers=_QQ_HEADERS, timeout=8,
        )
        resp.encoding = "utf-8"
        d = (resp.json().get("cdlist") or [{}])[0]
        songs = d.get("songlist") or []
        if not songs:
            return "错误：QQ 音乐歌单是空的，或接口暂时无响应。请稍后重试。"
        title = (d.get("dissname") or "").strip() or "我的歌单"
        creator = (d.get("nickname") or "").strip() or "GUEST"

        lines = [_receipt_header("QQMusic", title, creator)]
        shown = songs[:limit]
        total_ms = 0
        for i, s in enumerate(shown, start=1):
            artists = ", ".join(a.get("name", "") for a in (s.get("singer") or []))
            dur_ms = int(s.get("interval") or 0) * 1000   # interval 单位是秒
            total_ms += dur_ms
            lines.append(f"{i:02d}|{artists} - {s.get('songname', '')}|{_fmt_ms(dur_ms)}")
        lines.append(_receipt_footer(
            len(shown), _fmt_ms(total_ms),
            creator,     # CARD # → 歌单创建者用户名
            pid,         # AUTH CODE → 歌单 ID
            creator,     # CARDHOLDER → 歌单创建者用户名
        ))
        if len(songs) > limit:
            lines.append(f"歌单共 {len(songs)} 首，仅显示前 {limit} 首（可自行删改）")
        return "\n".join(lines)
    except Exception as e:
        return f"错误：QQ 音乐抓取失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def _extract_spotify_id(url: str):
    """从链接里抠 Spotify 歌单 ID（playlist/22位字符 或 spotify:playlist:22位）。"""
    m = re.search(r"playlist[:/]([A-Za-z0-9]{22})", url)
    return m.group(1) if m else None


def fetch_spotify_playlist(url: str, limit: int = 40) -> str:
    """
    Spotify 歌单链接 → 小票格式纯文本。
    接口：open.spotify.com/embed 页面里的 __NEXT_DATA__（匿名可用，实测有效）。
    trackList 每项含 title/subtitle(艺人)/duration(毫秒)。
    """
    try:
        pid = _extract_spotify_id(url)
        if pid is None:
            return (
                "错误：链接里找不到 Spotify 歌单 ID。\n"
                "请复制类似 open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M 的链接。"
            )
        resp = requests.get(
            f"https://open.spotify.com/embed/playlist/{pid}",
            headers=_HEADERS, timeout=12, proxies=_system_proxies(),
        )
        resp.encoding = "utf-8"
        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      resp.text, re.S)
        if not m:
            return "错误：Spotify 页面结构解析失败。请稍后重试，或手动输入。"
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        title = (entity.get("name") or "").strip() or "Spotify 歌单"
        track_list = entity.get("trackList") or []
        if not track_list:
            return "错误：Spotify 歌单是空的，或接口暂时无响应。请稍后重试。"

        # 创建者：attributes 里的 spotify:user:xxx；取第一段（如官方号 spotify），找不到就用 "Spotify"
        creator = "Spotify"
        for a in entity.get("attributes") or []:
            v = str(a.get("value") or "")
            if v.startswith("spotify:user:"):
                creator = v.split("spotify:user:")[-1].split(":")[0] or "Spotify"
                break

        lines = [_receipt_header("Spotify", title, creator)]
        shown = track_list[:limit]
        total_ms = 0
        for i, t_ in enumerate(shown, start=1):
            artists = t_.get("subtitle") or ""
            name = t_.get("title") or ""
            dur_ms = int(t_.get("duration") or 0)
            total_ms += dur_ms
            lines.append(f"{i:02d}|{artists} - {name}|{_fmt_ms(dur_ms)}")
        lines.append(_receipt_footer(
            len(shown), _fmt_ms(total_ms),
            creator,          # CARD # → 歌单创建者用户名
            _auth_code(pid),  # AUTH CODE → 歌单 ID 的数字指纹（6 位，票面不显示长串）
            creator,          # CARDHOLDER → 歌单创建者用户名
        ))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：Spotify 抓取失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def _extract_apple_music_id(url: str):
    """从链接里抠 Apple Music 歌单 ID（pl. 开头的一串字符）。"""
    m = re.search(r"pl\.[A-Za-z0-9]{10,}", url)
    return m.group(0) if m else None


def _iso_duration_to_ms(iso: str) -> int:
    """把 Apple 的 ISO8601 时长（PT2M56S）换算成毫秒。"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    h = int(m.group(1) or 0) if m else 0
    mi = int(m.group(2) or 0) if m else 0
    se = int(m.group(3) or 0) if m else 0
    return (h * 3600 + mi * 60 + se) * 1000


def fetch_apple_music_playlist(url: str, limit: int = 40) -> str:
    """
    Apple Music 歌单链接 → 小票格式纯文本。
    接口：music.apple.com 页面里的 schema:music-playlist JSON-LD（匿名可读）。
    注意：该 JSON-LD 只带歌名与时长，不带艺人字段，所以歌曲行只有歌名。
    """
    try:
        pid = _extract_apple_music_id(url)
        if pid is None:
            return (
                "错误：链接里找不到 Apple Music 歌单 ID。\n"
                "请复制类似 music.apple.com/cn/playlist/xxx/pl.fd06425... 的完整链接。"
            )
        resp = requests.get(url, headers=_HEADERS, timeout=12, proxies=_system_proxies())
        resp.encoding = "utf-8"
        m = re.search(r'<script id=schema:music-playlist[^>]*>(.*?)</script>',
                      resp.text, re.S)
        if not m:
            return "错误：Apple Music 页面解析失败。请稍后重试，或手动输入。"
        ld = json.loads(m.group(1))
        title = (ld.get("name") or "").strip() or "Apple Music 歌单"
        creator = ((ld.get("author") or {}).get("name") or "").strip() or "Apple Music"
        tracks = ld.get("track") or []
        if not tracks:
            return "错误：Apple Music 歌单是空的，或接口暂时无响应。请稍后重试。"

        lines = [_receipt_header("AppleMusic", title, creator)]
        shown = tracks[:limit]
        total_ms = 0
        for i, t_ in enumerate(shown, start=1):
            name = t_.get("name") or ""
            dur_ms = _iso_duration_to_ms(t_.get("duration") or "")
            total_ms += dur_ms
            lines.append(f"{i:02d}|{name}|{_fmt_ms(dur_ms)}")
        lines.append(_receipt_footer(
            len(shown), _fmt_ms(total_ms),
            creator,          # CARD # → 歌单创建者用户名
            _auth_code(pid),  # AUTH CODE → 歌单 ID 的数字指纹（6 位，票面不显示长串）
            creator,          # CARDHOLDER → 歌单创建者用户名
        ))
        return "\n".join(lines)
    except Exception as e:
        return f"错误：Apple Music 抓取失败（{e.__class__.__name__}）。请手动输入，或稍后重试。"


def fetch_music(url: str) -> str:
    """
    任意平台歌单/单曲链接统一入口：按域名自动分流。
    main.py 的「导入歌单」按钮调用这一个函数即可。
    """
    if "music.163.com" in url or "163cn.tv" in url:
        return fetch_netease(url)
    if "qq.com" in url:
        return fetch_qqmusic_playlist(url)
    if "spotify" in url:
        return fetch_spotify_playlist(url)
    if "apple.com" in url:
        return fetch_apple_music_playlist(url)
    return "错误：暂时只支持网易云 / QQ音乐 / Spotify / Apple Music 的歌单链接。"


# ============================================================
# 命令行自测入口：python net.py
# ============================================================
if __name__ == "__main__":
    print("【iTunes 搜索测试】周杰伦：")
    print(search_itunes("周杰伦", 3))
    print()
    print("【网易云歌单测试】用户提供的歌单：")
    print(fetch_netease_playlist("https://music.163.com/#/playlist?id=10045341103&uct2=x"))
    print()
    print("【网易云链接测试】一个无效链接：")
    print(fetch_netease_song("https://music.163.com/song/abc"))
