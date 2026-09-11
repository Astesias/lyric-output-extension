"""
timer.py - 歌词计时器模块
提供统一的时间接口，支持多种计时方案，可自由切换。

用法:
    from timer import WallClockTimer, SMTCTimer
    
    # 使用系统时钟方案（原始方案）
    timer = WallClockTimer()
    
    # 使用 SMTC 播放进度方案（更准确，需要 Windows SMTC 支持）
    timer = SMTCTimer()
    
    pos_ms = timer.get_position_ms()  # 获取当前播放位置（毫秒）
    timer.reset()                      # 歌曲切换时重置
"""

import time
import asyncio
import datetime
from threading import Thread
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager
)

# SMTC 播放状态枚举：GlobalSystemMediaTransportControlsSessionPlaybackStatus
_STATUS_CLOSED = 0
_STATUS_OPENED = 1
_STATUS_CHANGING = 2
_STATUS_STOPPED = 3
_STATUS_PLAYING = 4
_STATUS_PAUSED = 5


class WallClockTimer:
    """基于系统时间的计时器（原始方案）

    从 reset() 或创建时刻开始计时，用系统时钟推算播放进度。
    适合没有 SMTC 播放进度的场景，缺点是检测到歌曲的时机不准会导致偏差。
    """
    def __init__(self):
        self._start = time.time()

    def reset(self):
        """歌曲切换时重置计时起点"""
        self._start = time.time()

    def get_position_ms(self):
        """获取当前播放位置（毫秒）"""
        return (time.time() - self._start) * 1000


class SMTCTimer:
    """基于 SMTC 播放器进度的计时器（带插值，更准确）

    后台线程持续缓存 SMTC 播放进度，get_position_ms() 用本地时钟插值，
    避免 SMTC 更新间隔导致的「跳帧」。
    需要媒体播放器向 SMTC 报告进度（网易云 UWP / Edge / Chrome 等都支持）。

    关键改进（解决「有时歌词不同步」）：
    - 暂停/停止时冻结位置，不再继续插值前进（否则恢复播放后会错位）
    - 使用 SMTC 自带的 last_updated_time 作为插值锚点，而非轮询时刻，消除抖动
    - 支持 playback_rate（变速播放）
    - 检测跳转/大跨度 seek，立即校正而不逐帧追赶
    """
    def __init__(self, refresh_interval=0.2):
        self._cached_ms = 0.0                  # 锚点位置（毫秒）
        self._last_update_wall = time.time()   # 锚点对应的本地时间
        self._playing = False                  # 当前是否在播放
        self._rate = 1.0                       # 播放速率
        self._refresh_interval = refresh_interval
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 等事件循环启动后再提交后台任务
        time.sleep(0.05)
        asyncio.run_coroutine_threadsafe(self._background_refresh(), self._loop)

    def _run_loop(self):
        """在独立线程中长期运行事件循环"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @staticmethod
    def _to_ms(value):
        """将 SMTC 时间（TimeSpan / timedelta / 原始值）统一转换为毫秒"""
        if hasattr(value, 'total_seconds'):        # datetime.timedelta
            return value.total_seconds() * 1000
        if hasattr(value, 'duration'):             # winrt TimeSpan（100ns 单位）
            return value.duration / 10000
        return float(value) / 10000                # 原始 int（100ns 单位）

    async def _background_refresh(self):
        """后台持续刷新缓存位置与播放状态"""
        while True:
            try:
                sessions = await MediaManager.request_async()
                session = sessions.get_current_session()
                if session is not None:
                    timeline = session.get_timeline_properties()
                    base_ms = self._to_ms(timeline.position)

                    # ── 播放状态 & 速率 ──
                    playing = self._playing
                    rate = 1.0
                    try:
                        info = session.get_playback_info()
                        status = info.playback_status
                        playing = (int(status) == _STATUS_PLAYING)
                        try:
                            r = info.playback_rate
                            if r and r > 0:
                                rate = float(r)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # ── 用 SMTC 自带的 last_updated_time 校正锚点，消除轮询延迟抖动 ──
                    anchor_ms = base_ms
                    if playing:
                        try:
                            lut = timeline.last_updated_time  # datetime
                            if isinstance(lut, datetime.datetime):
                                now = (datetime.datetime.now(lut.tzinfo)
                                       if lut.tzinfo
                                       else datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
                                delta = (now - lut).total_seconds()
                                if 0 <= delta <= 5:   # 只接受合理范围，防止时钟异常
                                    anchor_ms = base_ms + delta * 1000 * rate
                        except Exception:
                            pass

                    self._cached_ms = anchor_ms
                    self._last_update_wall = time.time()
                    self._playing = playing
                    self._rate = rate
            except Exception:
                pass
            await asyncio.sleep(self._refresh_interval)

    def reset(self):
        """切歌时重置插值基点，避免旧位置残留"""
        self._cached_ms = 0.0
        self._last_update_wall = time.time()
        self._playing = False

    def get_position_ms(self):
        """获取当前播放位置（毫秒）

        - 播放中：锚点位置 + 本地时钟流逝 × 速率（平滑插值）
        - 暂停/停止：直接返回冻结的锚点位置，避免歌词继续前进导致错位
        """
        if not self._playing:
            return self._cached_ms
        elapsed = (time.time() - self._last_update_wall) * 1000
        return self._cached_ms + elapsed * self._rate

    @property
    def playing(self):
        """当前是否在播放"""
        return self._playing

    def stop(self):
        """停止事件循环"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


def create_timer(mode='wall_clock'):
    """创建计时器的工厂函数

    Args:
        mode: 'wall_clock'（系统时钟）或 'smtc'（SMTC 播放进度）

    Returns:
        WallClockTimer 或 SMTCTimer 实例
    """
    if mode == 'smtc':
        return SMTCTimer()
    return WallClockTimer()
