"""
lyric_cache.py - 歌词缓存模块

将已获取的歌词缓存到本地 JSON 文件，避免对同一首歌重复发起 API 请求。
缓存键基于歌曲标题（含歌手名）生成，值包含原文歌词、翻译歌词、合并后的歌词列表等。

用法:
    from lyric_cache import LyricCache

    cache = LyricCache()
    data = cache.get("夜曲 - 周杰伦")
    if data:
        script = data["script"]
        t_script = data["t_script"]
        merged_lyr = data["merged_lyr"]
    else:
        # ... 调用 API 获取 ...
        cache.set("夜曲 - 周杰伦", script, t_script, merged_lyr, songid)
"""

import os
import json
import hashlib
import time


import sys


class LyricCache:
    """歌词缓存管理器

    将已获取的歌词持久化到本地 JSON 文件。
    缓存键为歌曲标题的 SHA256 哈希，值保存完整的歌词数据。

    属性:
        MAX_ENTRIES: 最大缓存条目数，超过时自动淘汰最旧条目
        CACHE_FILE: 缓存文件的路径
    """

    CACHE_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'lyric_cache.json'
    )
    MAX_ENTRIES = 200

    def __init__(self):
        self._cache = {}
        self._load()

    # ── 内部方法 ──

    @staticmethod
    def _make_key(title: str) -> str:
        """根据歌曲标题生成定长缓存键（SHA256）"""
        return hashlib.sha256(title.encode('utf-8')).hexdigest()

    def _load(self):
        """从磁盘加载缓存"""
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"[缓存] 加载缓存失败: {e}", file=sys.stderr)
                self._cache = {}

    def _save(self):
        """将缓存写入磁盘"""
        try:
            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[缓存] 写入缓存失败: {e}", file=sys.stderr)

    # ── 公共接口 ──

    def get(self, title: str):
        """获取指定歌曲的缓存歌词

        Args:
            title: 歌曲标题，格式如 "夜曲 - 周杰伦"

        Returns:
            dict | None:
            {
                "title": "夜曲 - 周杰伦",
                "script": "[00:00.000] 原歌词...",
                "t_script": "[00:00.000] 翻译...",
                "merged_lyr": [[时间戳, "原文", "翻译"], ...],
                "songid": 123456,
                "timestamp": 1700000000.0
            }
            缓存未命中或标题不匹配时返回 None。
        """
        key = self._make_key(title)
        entry = self._cache.get(key)
        if entry and entry.get('title') == title:
            return entry
        return None

    def set(self, title: str, script: str, t_script: str,
            merged_lyr: list, songid: int):
        """将歌词数据存入缓存

        Args:
            title: 歌曲标题
            script: 原文 LRC 文本
            t_script: 翻译 LRC 文本（可能为空）
            merged_lyr: merge_lyrics() 后的合并歌词列表
            songid: 网易云歌曲 ID
        """
        key = self._make_key(title)
        self._cache[key] = {
            'title': title,
            'script': script,
            't_script': t_script,
            'merged_lyr': merged_lyr,
            'songid': songid,
            'timestamp': time.time(),
        }

        # 超过最大容量时淘汰最旧的条目
        if len(self._cache) > self.MAX_ENTRIES:
            sorted_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k].get('timestamp', 0)
            )
            for old_key in sorted_keys[:len(self._cache) - self.MAX_ENTRIES]:
                del self._cache[old_key]

        self._save()

    def clear(self):
        """清空所有缓存"""
        self._cache = {}
        self._save()

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        return len(self._cache)
