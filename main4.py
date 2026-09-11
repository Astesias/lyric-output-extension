import time
import os
import sys
import json
import re
import base64
import binascii
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from threading import Thread
from soc import data          # Flask 共享数据，用于 Web 服务器输出
from utils import chained_request, mute_all
from timer import WallClockTimer, SMTCTimer
from lyric_cache import LyricCache
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager
)

# ── 设置控制台/管道输出编码为 UTF-8 ──
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        print('stdout reconfigure failed')

# ── 从外部配置文件读取 Cookie ──
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    CSRF_TOKEN = cfg['csrf_token']          # 网易云 API 的 csrf token
    COOKIE = cfg['cookie']                   # 登录后的 cookie
    USER_AGENT = cfg.get('user_agent', "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0")
    TIMER_MODE = cfg.get('timer_mode', 'smtc')
    SHOW_TITLE_IN_LYRICS = cfg.get('show_title_in_lyrics', False)
    # 配置已加载
else:
    # config.json 不存在，使用默认值（API 将返回需要登录的错误）
    CSRF_TOKEN = ""
    COOKIE = ""
    USER_AGENT = "Mozilla/5.0"
    TIMER_MODE = 'smtc'
    SHOW_TITLE_IN_LYRICS = False

# ── HTTP 请求头 ──
HEADER = {
    "accept": "*/*",
    "content-type": "application/x-www-form-urlencoded",
    "cookie": COOKIE,
    "origin": "https://music.163.com",
    "referer": "https://music.163.com/search/",
    "user-agent": USER_AGENT,
}

# ── 网易云 API 端点 ──
URL_SEARCH = f"https://music.163.com/weapi/cloudsearch/get/web?csrf_token={CSRF_TOKEN}"
URL_LYR = f"https://music.163.com/weapi/song/lyric?csrf_token={CSRF_TOKEN}"

# ── 是否禁用 debug 日志 ──
DISABLELOG = False


def printd(*args, **kws):
    """调试输出，DISABLELOG=True 时静音"""
    if not DISABLELOG:
        print(*args, **kws)


async def get_media_info():
    """通过 Windows SMTC 获取当前播放媒体信息（异步）"""
    try:
        sessions = await MediaManager.request_async()
        session = sessions.get_current_session()
        if session:
            props = await session.try_get_media_properties_async()
            return props.title, props.artist or ''
    except Exception as e:
        printd(f"SMTC error: {e}")
    return None, ''


def get_media_info_sync():
    """获取当前播放媒体信息（同步包装）"""
    try:
        return asyncio.run(get_media_info())
    except Exception as e:
        printd(f"SMTC sync error: {e}")
    return None, ''


class RealtimeCloudMusicLyric:
    """网易云音乐实时歌词同步核心类
    
    功能：
    - 通过 SMTC 监听当前播放歌曲
    - 调用网易云 API 搜索歌曲并获取歌词
    - 解析 LRC 格式歌词（原文 + 翻译）
    - 按时间戳实时推进并输出上下文（前2句 + 当前 + 后2句）
    """
    def __init__(self, title):
        # ── 搜索请求参数 ──
        self.data_search = {
            "hlpretag": '<span class="s-fc7">',
            "hlposttag": "</span>",
            "id": "2100334024",
            "s": "",          # 待搜索的歌曲名
            "type": "1",        # 1=单曲
            "offset": "0",
            "total": "true",
            "limit": "30",
            "csrf_token": CSRF_TOKEN,
        }
        # ── 歌词请求参数 ──
        self.data_lyric = {
            "id": None,         # 搜索后填入歌曲 ID
            "lv": -1,           # 原文歌词版本
            "tv": -1,           # 翻译歌词版本
            "csrf_token": CSRF_TOKEN,
        }

        self._title = title                     # 当前歌曲名
        self._old_smtc_title = None             # 上一次的 SMTC 标题（用于检测切换）
        self._lyr_index = -1                    # 原文歌词索引
        self._tlyr_index = -1                   # 翻译歌词索引
        self.t_lyr = {}                         # 歌词元数据
        self._updating = False                  # 是否正在更新歌词
        self.new_title = True                   # 是否新歌曲
        self._timer = SMTCTimer() if TIMER_MODE == 'smtc' else WallClockTimer()
        self._close = False                     # 关闭信号
        self._cache = LyricCache()              # 歌词缓存
        self._title_sent = True                 # 初始歌名待输出

        # ── 启动后台线程监听歌曲切换 ──
        _thread = Thread(target=self._listen_change, daemon=True)
        _thread.start()
        self._thread = _thread

    # ═══════════════ 网易云 API 加密 ═══════════════

    def _jm1(self, data, key, iv):
        """AES-CBC 加密（网易云 WeAPI 第一层加密）"""
        key = key.encode("utf-8")
        data1 = data.encode("utf-8")
        data1 = pad(data1, 16)
        aes = AES.new(key=key, mode=AES.MODE_CBC, IV=iv)
        res = aes.encrypt(data1)
        return base64.b64encode(res).decode()

    def _jm2(self, i, e, f):
        """RSA 加密（网易云 WeAPI 第二层加密）"""
        e = int(e, 16)
        f = int(f, 16)
        i = i[::-1]
        bs = i.encode("utf-8")
        s = binascii.b2a_hex(bs).decode()
        s = int(s, 16)
        mi = pow(s, e, f)  # 模幂运算，远快于 (s**e) % f
        return format(mi, "x")

    def _asrsea(self, data, a, e, f):
        """网易云 WeAPI 完整加密流程（两层 AES + RSA）"""
        i = "xS5OktXaZxEDoVUb"
        key = f
        data1 = self._jm1(data, key, b"0102030405060708")       # 第一层 AES
        data2 = self._jm1(data1, i, b"0102030405060708")       # 第二层 AES
        jmhd_i = self._jm2(i, a, e)                              # RSA 加密
        return data2, jmhd_i

    def _get_param(self, data):
        """构造 API 请求参数 {params, encSecKey}"""
        data = json.dumps(data)
        cs1, cs2 = self._asrsea(
            data, "010001",
            "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
            "0CoJUm6Qyw8W8jud",
        )
        return {"params": cs1, "encSecKey": cs2}

    # ═══════════════ 时间工具 ═══════════════

    def _time_trans_ms(self, t4):
        """将 [时,分,秒,毫秒] 转换为毫秒数"""
        h, m, s, *ms = t4
        if ms:
            ms = ms[0]
        else:
            ms = 0
        return h * 3600 * 1000 + m * 60 * 1000 + s * 1000 + ms

    # ═══════════════ 歌曲监听 ═══════════════

    def _listen_change(self):
        """后台线程：每秒检查 SMTC 播放状态，检测到歌曲切换时自动加载新歌词"""
        while not self._close:
            try:
                smtc_title, artist = get_media_info_sync()
                if smtc_title and smtc_title != self._old_smtc_title:
                    combined = f"{smtc_title} - {artist}" if artist else smtc_title
                    self._old_smtc_title = smtc_title
                    if self._title != combined:
                        self._title = combined
                        self.new_title = True
                        self._title_sent = True     # 标记歌名待输出
                        self._timer.reset()
                        self.update_song()
            except Exception as e:
                printd(f"SMTC listen error: {e}")
            time.sleep(1)

    # ═══════════════ 网易云 API 调用 ═══════════════

    def _get_current_song_id(self):
        """通过歌曲名搜索，获取网易云歌曲 ID"""
        self.data_search["s"] = self._title
        payload_search = self._get_param(self.data_search)
        r = chained_request(URL_SEARCH).ajax().payload(payload_search).headers(HEADER).post().json()
        if 'result' not in r or not r['result'].get('songs'):
            # 完整标题搜不到，尝试只用歌曲名（去掉歌手）
            safe_print(f"[警告] 搜索不到歌曲，响应: code={r.get('code')}, keys={list(r.keys())}")
            simple_title = self._title.split(' - ')[0] if ' - ' in self._title else self._title
            self.data_search["s"] = simple_title
            payload_search2 = self._get_param(self.data_search)
            r = chained_request(URL_SEARCH).ajax().payload(payload_search2).headers(HEADER).post().json()
            if 'result' not in r or not r['result'].get('songs'):
                raise KeyError(f"搜索失败: {r}")
        self.songid = r["result"]["songs"][0].get("privilege", {}).get("id") or r["result"]["songs"][0]["id"]

    def _get_current_song_lyr(self):
        """获取当前歌曲的歌词（原文 + 翻译），并写入日志文件"""
        self.data_lyric["id"] = self.songid
        payload_lyr = self._get_param(self.data_lyric)
        r = chained_request(URL_LYR).ajax().headers(HEADER).payload(payload_lyr).post().json()
        # ── 解析歌词 ──
        self.script = r["lrc"]["lyric"]          # 原文 LRC
        try:
            self.t_script = r["tlyric"]["lyric"]  # 翻译 LRC
        except:
            printd('t_script 获取失败')
            self.t_script = ''

    def _parse_single_lyrics(self, script):
        result = []
        for l in script.split("\n"):
            if l:
                line = l
                m = re.match("\[.+\]", line)
                if m:
                    t = m.group().replace("[", "00:").replace("]", "").replace(".", ":")
                    w = line[m.end():]
                    if t.count(':') < 3:
                        continue
                    try:
                        t4 = list(map(int, t.split(":")))
                        result.append((self._time_trans_ms(t4), w))
                    except Exception as e:
                        printd(f'解析歌词行失败: {e}, line={repr(l[:30])}')
        return result

    def _merge_lyrics(self, original, translation):
        """将翻译歌词配对到原文歌词，仅当时间戳接近时"""
        if not translation:
            return [(t, o, '') for t, o in original]

        # 为每个翻译行找到最接近的原文行（时间差 < 1 秒）
        orig_paired = set()
        trans_to_orig = {}  # 翻译行索引 -> 原文行索引
        for ti, (tt, _) in enumerate(translation):
            best_oi = -1
            best_dist = 1000
            for oi, (ot, _) in enumerate(original):
                if oi in orig_paired:
                    continue
                dist = abs(tt - ot)
                if dist < best_dist:
                    best_dist = dist
                    best_oi = oi
            if best_oi >= 0:
                trans_to_orig[ti] = best_oi
                orig_paired.add(best_oi)

        merged = []
        for oi, (ot, orig_text) in enumerate(original):
            trans_text = ''
            for ti, oi2 in trans_to_orig.items():
                if oi2 == oi:
                    trans_text = translation[ti][1]
                    break
            merged.append((ot, orig_text, trans_text))  # (时间戳, 原文, 翻译)
        return merged

    # ═══════════════ 歌词更新与迭代 ═══════════════

    def update_song(self):
        """完整更新流程：搜索歌曲 ID → 获取歌词 → 解析 → 合并翻译

        优先从本地缓存加载歌词，缓存未命中时再调用 API。
        """
        self._updating = True

        # ── 尝试从缓存加载 ──
        cached = self._cache.get(self._title)
        if cached:
            self.songid = cached['songid']
            self.script = cached['script']
            self.t_script = cached['t_script']
            # JSON 反序列化后 tuple 会变成 list，但下标访问方式一致
            self.merged_lyr = cached['merged_lyr']
            original = self._parse_single_lyrics(self.script)
            self.t_lyr = {"title": self._title, "lyc": original,
                          "maxtime": original[-1][0] if original else 0}
            self._merged_index = 0
            self._updating = False
            return

        # ── 缓存未命中，调用 API ──
        self._get_current_song_id()
        self._get_current_song_lyr()

        original = self._parse_single_lyrics(self.script)
        translation = self._parse_single_lyrics(self.t_script) if self.t_script else []

        self.merged_lyr = self._merge_lyrics(original, translation)
        self.t_lyr = {"title": self._title, "lyc": original,
                      "maxtime": original[-1][0] if original else 0}

        # ── 写入缓存 ──
        self._cache.set(self._title, self.script, self.t_script,
                        self.merged_lyr, self.songid)

        self._merged_index = 0
        self._updating = False

    def _find_lyric_index(self, position_ms):
        """二分查找：根据当前播放位置（毫秒）定位歌词索引

        返回第一个时间戳 > position_ms 的行索引，即下一句待唱的歌词位置。
        """
        if not self.merged_lyr:
            return 0
        lo, hi = 0, len(self.merged_lyr)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.merged_lyr[mid][0] <= position_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def lyr_iter(self, offset):
        """迭代歌词：当系统时间超过歌词时间戳时，返回 (原文, 翻译) 并推进索引

        支持任意位置跳转（进度条回退 / 前跳 / 播放器卡顿）：
        - 回退：当前播放位置早于上一句已唱歌词 → 重新定位
        - 前跳/卡顿：当前播放位置远超下一句待唱歌词（>1.5s）→ 重新定位
        跳转后立即重绘当前正在唱的那一句，避免逐行追赶造成的错位。

        参数:
            offset: 时间偏移（秒），负数=更晚触发（感觉慢了），正数=更早触发（感觉快了）
        """
        if not self.merged_lyr and self._updating:
            return ('歌词加载中...', '')
        if not self.merged_lyr:
            return None

        atime = self._timer.get_position_ms() + offset * 1000

        # ── 跳转检测 ──
        jumped = False
        if self._merged_index > 0 and atime < self.merged_lyr[self._merged_index - 1][0]:
            # 回退：播放位置早于上一句已唱歌词
            jumped = True
        elif (self._merged_index < len(self.merged_lyr)
              and atime > self.merged_lyr[self._merged_index][0] + 1500):
            # 前跳/卡顿：播放位置远超下一句待唱歌词，说明跳过了多句
            jumped = True

        if jumped:
            new_idx = self._find_lyric_index(atime)
            self._merged_index = new_idx
            # 立即重绘当前正在唱的那一句（新索引的前一句），不推进索引
            if new_idx > 0:
                _, orig, trans = self.merged_lyr[new_idx - 1]
                return (orig, trans)
            return None

        if self._merged_index >= len(self.merged_lyr):
            # 歌曲播完后回退：重新定位索引并立即重绘
            if atime < self.merged_lyr[-1][0]:
                self._merged_index = self._find_lyric_index(atime)
                if self._merged_index > 0:
                    _, orig, trans = self.merged_lyr[self._merged_index - 1]
                    return (orig, trans)
            return None  # 确实已播完

        itime = self.merged_lyr[self._merged_index][0]
        if atime > itime:
            _, orig, trans = self.merged_lyr[self._merged_index]
            self._merged_index += 1
            return (orig, trans)
        return None

    def get_display_context(self):
        """获取当前歌词显示上下文：始终返回 2 行 prev + 1 行 current + 2 行 next

        - current: 刚唱完/正在唱的那一句（从 idx-1 往左找第一个非空行）
        - prev: current 再往左的 2 个非空行
        - next: 从 idx 往右的 2 个非空行
        - 自动跳过无歌词内容的空行
        """
        idx = self._merged_index
        total = len(self.merged_lyr) if self.merged_lyr else 0

        def _has_content(i):
            return 0 <= i < total and (self.merged_lyr[i][1] or self.merged_lyr[i][2])

        def _scan(start, direction, count):
            """从 start 开始沿 direction 扫描 count 个非空行"""
            result = []
            i = start
            while len(result) < count and 0 <= i < total:
                if _has_content(i):
                    result.append((self.merged_lyr[i][1], self.merged_lyr[i][2]))
                i += direction
            while len(result) < count:
                result.append(('', ''))
            return result

        # current = 往左找第一个非空行
        current_idx = -1
        for i in range(idx - 1, -1, -1):
            if _has_content(i):
                current = (self.merged_lyr[i][1], self.merged_lyr[i][2])
                current_idx = i
                break
        else:
            current = ('', '')

        # prev = 从 current 再往左找 2 个非空行
        raw_prev = _scan(current_idx - 1, -1, 2)
        raw_prev.reverse()
        prev_lines = raw_prev
        # next = 从 idx 往右找 2 个非空行
        next_lines = _scan(idx, 1, 2)

        return prev_lines, current, next_lines

    def joinready(self):
        with mute_all():
            self.update_song()

    def close(self):
        self._close = True


def safe_print(text, end='\n'):
    """安全打印，自动处理编码错误，强制立即刷新"""
    try:
        print(text, end=end, flush=True)
    except UnicodeEncodeError:
        try:
            print(text.encode('utf-8', errors='replace').decode('utf-8'), end=end, flush=True)
        except Exception as e:
            printd(f'safe_print fallback failed: {e}')


# ANSI 转义码：终端样式（通过 --plain 禁用颜色）
USE_COLOR = '--plain' not in sys.argv

class Style:
    """终端样式，USE_COLOR=False 时全部置空"""
    _map = {
        'RESET': '\033[0m', 'DIM': '\033[2m', 'BOLD': '\033[1m',
        'CYAN': '\033[36m', 'GRAY': '\033[90m', 'BLUE': '\033[34m',
        'CLEAR': '\033[2J\033[H',
        'HIDE_CURSOR': '\033[?25l', 'SHOW_CURSOR': '\033[?25h',
    }
    if not USE_COLOR:
        for k in _map:
            _map[k] = ''

STYLE_RESET = Style._map['RESET']
STYLE_DIM = Style._map['DIM']
STYLE_BOLD = Style._map['BOLD']
STYLE_CYAN = Style._map['CYAN']
STYLE_GRAY = Style._map['GRAY']
STYLE_BLUE = Style._map['BLUE']
CLEAR_SCREEN = '\033[2J\033[H'  # 不受 --plain 影响，始终输出
STYLE_HIDE_CURSOR = Style._map['HIDE_CURSOR']
STYLE_SHOW_CURSOR = Style._map['SHOW_CURSOR']


def _lyr_line(orig, trans, prefix, style, trans_prefix=None):
    """构建一行歌词（原文+翻译），prefix 控制缩进，style 控制颜色"""
    if not orig and not trans:
        return ''
    if trans_prefix is None:
        trans_prefix = prefix
    if USE_COLOR:
        if orig:
            line = f"{prefix}{style}{orig}{STYLE_RESET}"
            # 无论有无翻译，都输出翻译行（空行用空格占位保持对齐）
            trans_style = STYLE_BLUE if style == STYLE_BLUE else STYLE_RESET
            trans_text = trans if trans else ''
            line += f"\n{trans_prefix}{trans_style}{trans_text}{STYLE_RESET}"
        else:
            # 仅翻译，不输出空原文行
            line = f"{trans_prefix}{STYLE_BLUE if style == STYLE_BLUE else STYLE_RESET}{trans}{STYLE_RESET}"
    else:
        if orig:
            line = f"{prefix}{orig}"
            # 无论有无翻译，都输出翻译行
            line += f"\n{trans_prefix}{trans if trans else ''}"
        else:
            line = f"{trans_prefix}{trans}"
    return line


def render_scroll(prev, current, next_lines, title, show_title=True):
    """全量渲染，每两句歌词间空一行；show_title=False 时跳过歌名"""
    lines = [f"🎵 {title}", ''] if show_title else []
    for idx, (o, t) in enumerate(prev):
        prefix = '\t\t' if idx == 0 else '\t'  # 前第2行多一个\t
        l = _lyr_line(o, t, prefix, '')
        if l:
            lines.append(l)
            lines.append('')
    orig, trans = current
    l = _lyr_line(orig, trans, '', STYLE_BLUE)
    if l:
        lines.append(l)
        lines.append('')
    for idx, (o, t) in enumerate(next_lines):
        prefix = '\t\t' if idx == 1 else '\t'  # 后第2行多一个\t
        l = _lyr_line(o, t, prefix, '')
        if l:
            lines.append(l)
            lines.append('')
    safe_print('\n'.join(lines))


if __name__ == '__main__':
    # 解析命令行参数: -s [port] 开启 Web 服务器, -offset <秒> 叠加时间偏移
    start_server = False
    server_port = 62333
    cli_offset = 0.0
    if '-s' in sys.argv:
        start_server = True
        idx = sys.argv.index('-s')
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
            server_port = int(sys.argv[idx + 1])
    if '-offset' in sys.argv:
        idx = sys.argv.index('-offset')
        if idx + 1 < len(sys.argv):
            try:
                cli_offset = float(sys.argv[idx + 1])
            except ValueError:
                safe_print(f"[警告] -offset 参数无效，已忽略: {sys.argv[idx + 1]}")

    title = None
    while not title:
        t, a = get_media_info_sync()
        if t:
            title = f"{t} - {a}" if a else t
            break
        else:
            time.sleep(3)

    r = RealtimeCloudMusicLyric(title)
    r.joinready()

    # 仅在指定 -s 参数时启动 Web 服务器
    server_thread = None
    if start_server:
        from soc import run as run_server
        # 启动 Web 服务器
        server_thread = Thread(target=lambda: run_server(port=server_port), daemon=True)
        server_thread.start()
    else:
        # 提示: 加 -s 参数可启动 Web 服务器
        pass
    # 时间偏移 = 配置文件值 + 命令行参数
    cfg_ofs = cfg.get('time_offset', -0.6) if os.path.exists(CONFIG_FILE) else -0.6
    ofs = cfg_ofs + cli_offset
    last_time = -1  # 防止重复打印相同歌词

    try:
        # 首次清屏，输出歌名（只一次），之后自然向下滚动
        safe_print(f"{CLEAR_SCREEN}{STYLE_HIDE_CURSOR}", end='')
        safe_print('\n\n\n\n\n', end='')  # 开头 5 行留白，避免歌词顶格
        while 1:
            maxtime = r.t_lyr.get('maxtime', 300000)
            nowtime = r._timer.get_position_ms() + ofs * 1000
            data["AllTime"] = maxtime
            data["Now"] = nowtime if nowtime < maxtime else maxtime

            # 歌名只在新歌切换时输出/设置一次（独立行，不混杂 CLEAR_SCREEN）
            if r._title_sent:
                data['Title'] = r._title
                r._title_sent = False
                safe_print(f"🎵 {r._title}")

            result = r.lyr_iter(ofs)

            if result:
                orig, trans = result
                if id(orig) != last_time:
                    last_time = id(orig)
                    if orig:
                        data['Lryic'] = orig
                        data['ChineseLryic'] = trans or ''

                    if SHOW_TITLE_IN_LYRICS:
                        prev, curr, nxt = r.get_display_context()
                        safe_print(f"{CLEAR_SCREEN}", end='')
                        render_scroll(prev, curr, nxt, r._title, show_title=True)
                    else:
                        prev, curr, nxt = r.get_display_context()
                        safe_print(f"{CLEAR_SCREEN}", end='')
                        render_scroll(prev, curr, nxt, r._title, show_title=False)
            time.sleep(0.1)

        safe_print(f"{STYLE_SHOW_CURSOR}")
    except KeyboardInterrupt:
        safe_print(f"\n{STYLE_SHOW_CURSOR}⏹ 正在退出...")
    except SystemExit:
        safe_print(f"\n{STYLE_SHOW_CURSOR}⏹ 正在退出...")
    except Exception as e:
        printd(f'运行异常: {e}')
    finally:
        safe_print(f"{STYLE_SHOW_CURSOR}")
        safe_print('正在停止监听线程...')
        r.close()
        if server_thread and server_thread.is_alive():
            import requests as _req
            try:
                _req.post(f'http://127.0.0.1:{server_port}/shutdown', timeout=1)
            except:
                pass
        safe_print('✅ 已安全退出')
