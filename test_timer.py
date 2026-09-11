"""
测试 SMTCTimer 与 WallClockTimer 的计时效果。
运行前请确保网易云音乐或 Edge/Chrome 正在播放歌曲。
"""
import time
import sys
import os

# 确保能找到同目录的 timer 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from timer import WallClockTimer, SMTCTimer


def test_timer(name, timer, duration=15, label=''):
    """测试计时器，每 0.5 秒采样一次并打印"""
    print(f"\n{'='*50}")
    print(f"📊 测试: {name}")
    print(f"{'='*50}")
    if label:
        print(f"🎵 当前歌曲: {label}")
    print(f"{'采样(s)':>8} {'位置(ms)':>10} {'进度':>8}")
    print("-" * 30)

    timer.reset()
    prev_ms = 0

    for i in range(duration * 2):  # 0.5s 间隔
        ms = timer.get_position_ms()
        delta = ms - prev_ms if prev_ms > 0 else 0
        prev_ms = ms

        sec = i * 0.5
        bar_len = min(int(ms / 500), 20)
        bar = '█' * bar_len + '░' * (20 - bar_len)

        print(f"{sec:>8.1f} {ms:>10.0f} {bar:>8}  +{delta:>5.0f}ms")

        time.sleep(0.5)

    return prev_ms


def compare_timers(duration=10):
    """同时运行两个计时器对比"""
    print(f"\n{'🔥'*25}")
    print(f"   双计时器对比（{duration} 秒）")
    print(f"{'🔥'*25}")
    print(f"{'采样(s)':>8} {'WallClock(ms)':>14} {'SMTC(ms)':>14} {'差值(ms)':>10}")
    print("-" * 50)

    wt = WallClockTimer()
    st = SMTCTimer()
    wt.reset()

    for i in range(duration * 2):
        wms = wt.get_position_ms()
        sms = st.get_position_ms()
        diff = wms - sms
        sec = i * 0.5
        print(f"{sec:>8.1f} {wms:>14.0f} {sms:>14.0f} {diff:>+10.0f}")
        time.sleep(0.5)

    return wt, st


if __name__ == '__main__':
    # print("⏱  SMTCTimer 测试脚本")
    # print("   请确保有媒体正在播放（网易云/Edge/Chrome 等）")
    # print("   按 Ctrl+C 随时退出\n")

    # ── 测试 1: WallClockTimer 单独测 ──
    # test_timer("WallClockTimer（系统时钟）", WallClockTimer(), duration=5)

    # ── 测试 2: SMTCTimer 单独测 ──
    # test_timer("SMTCTimer（SMTC 播放进度）", SMTCTimer(), duration=5)

    # ── 测试 3: 两者对比 ──
    compare_timers(duration=8)

    print("\n✅ 测试完成")
