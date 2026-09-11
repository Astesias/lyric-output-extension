"""
调试 SMTCTimer — 逐步检查 SMTC 各个返回值
"""
import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager
)


async def debug_smtc():
    print("=" * 60)
    print("🔍 SMTC 调试 — 逐步检查")
    print("=" * 60)

    # 1. 获取 SessionManager
    print("\n[1] MediaManager.request_async()...")
    try:
        sessions = await MediaManager.request_async()
        print(f"    ✅ sessions = {sessions}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        return

    # 2. 获取当前 Session
    print("\n[2] sessions.get_current_session()...")
    try:
        session = sessions.get_current_session()
        print(f"    ✅ session = {session}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        session = None

    # 2b. 枚举所有 Session
    print("\n[2b] sessions.get_sessions()...")
    try:
        all_sessions = sessions.get_sessions()
        print(f"    ✅ get_sessions() 类型 = {type(all_sessions)}")
        count = 0
        for s in all_sessions:
            count += 1
            try:
                p = await asyncio.wait_for(s.try_get_media_properties_async(), timeout=3)
                print(f"    🎵 Session #{count}: source={s.source_app_user_model_id}, title={p.title}")
            except Exception as e:
                print(f"    🎵 Session #{count}: source={s.source_app_user_model_id}, error={e}")
        print(f"    📊 共 {count} 个 session")
    except Exception as e:
        print(f"    ❌ 失败: {e}")

    if session is None and count == 0:
        print("    ❌ 没有任何活动 session！请打开网易云/Edge 播放歌曲后重试")
        return
    
    # 3. 获取媒体属性（标题等）
    print("\n[3] session.try_get_media_properties_async()...")
    try:
        props = await session.try_get_media_properties_async()
        print(f"    ✅ title  = {props.title}")
        print(f"    ✅ artist = {props.artist}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")

    # 4. 获取时间线属性
    print("\n[4] session.get_timeline_properties()...")
    try:
        timeline = session.get_timeline_properties()
        print(f"    ✅ timeline = {timeline}")
        print(f"    ✅ dir(timeline) = {[x for x in dir(timeline) if not x.startswith('_')]}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        return

    # 5. 检查 position
    print("\n[5] timeline.position...")
    try:
        pos = timeline.position
        print(f"    ✅ position = {pos!r}")
        print(f"    ✅ type     = {type(pos)}")
        print(f"    ✅ dir(pos) = {[x for x in dir(pos) if not x.startswith('_')]}")
        
        # 尝试各种方式获取数值
        if hasattr(pos, 'total_seconds'):
            print(f"    ✅ pos.total_seconds() = {pos.total_seconds()}")
        if hasattr(pos, 'duration'):
            print(f"    ✅ pos.duration = {pos.duration}")
        if hasattr(pos, 'total_milliseconds'):
            print(f"    ✅ pos.total_milliseconds = {pos.total_milliseconds()}")
        
        print(f"    ✅ float(pos) = {float(pos)}")
        print(f"    ✅ int(pos)   = {int(pos)}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")

    # 6. 检查 end_time（总时长）
    print("\n[6] timeline.end_time...")
    try:
        end = timeline.end_time
        print(f"    ✅ end_time = {end!r}")
        if hasattr(end, 'total_seconds'):
            print(f"    ✅ end_time.total_seconds() = {end.total_seconds()}")
        if hasattr(end, 'duration'):
            print(f"    ✅ end_time.duration = {end.duration}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")

    # 7. 检查 playback 状态
    print("\n[7] session.get_playback_info()...")
    try:
        info = session.get_playback_info()
        print(f"    ✅ playback_info = {info}")
        print(f"    ✅ dir = {[x for x in dir(info) if not x.startswith('_')]}")
        if hasattr(info, 'playback_status'):
            print(f"    ✅ playback_status = {info.playback_status}")
    except Exception as e:
        print(f"    ❌ 失败: {e}")

    print("\n" + "=" * 60)
    print("调试完成")
    print("=" * 60)


if __name__ == '__main__':
    asyncio.run(debug_smtc())
