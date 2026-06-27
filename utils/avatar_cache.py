"""
头像下载与缓存模块

负责从微信联系人数据库中提取头像 URL，下载并缓存为本地图片文件，
供 Windows Toast 通知（winotify）作为通知图标使用。

特性：
- 自动识别头像 URL 字段（兼容微信 4.x 多种列名）
- 本地磁盘缓存，避免重复下载
- 失败记录，避免短时间内反复重试
- 使用 Pillow 将头像规整为正方形 PNG，保证通知图标显示效果
- 线程安全的下载与写入
"""
import os
import io
import time
import threading
import urllib.request
import urllib.error
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger("WeChatNotifier")

# 头像 URL 在 contact 表中可能的列名（按优先级）
_AVATAR_URL_COLUMNS = [
    "small_head_img_url",   # 微信 4.x 小头像 URL
    "big_head_img_url",     # 微信 4.x 大头像 URL
    "avatar_url",           # 通用命名
    "head_img_url",
    "small_avatar_url",
    "avatar",
]

# 免打扰状态在 contact 表中可能的列名
_MUTE_COLUMNS = [
    "notification_on",      # 部分版本：0 表示免打扰
    "mute_notification",
    "is_mute",
    "is_muted",
    "mute",
]

# 下载超时（秒）
_DOWNLOAD_TIMEOUT = 4
# 缓存有效期（秒）：超过后允许重新下载（用于头像更新）
_CACHE_TTL = 7 * 24 * 3600
# 失败冷却时间（秒）：下载失败后短时间内不重试
_FAIL_COOLDOWN = 10 * 60
# 头像输出尺寸（正方形）
_AVATAR_SIZE = 96


class AvatarCache:
    """联系人/群头像的下载与缓存管理器"""

    def __init__(self, cache_dir: str, default_icon: Optional[str] = None):
        """
        Args:
            cache_dir: 头像缓存目录
            default_icon: 默认图标路径（下载失败时使用）
        """
        self.cache_dir = os.path.join(cache_dir, "avatars")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.default_icon = default_icon

        # 内存缓存：username -> 本地图片路径（命中后直接返回）
        self._path_cache = {}
        # 失败记录：username -> 失败时间戳
        self._fail_map = {}
        # 下载锁，避免同一头像并发下载
        self._locks = {}
        self._locks_guard = threading.Lock()

        # 微信联系人头像 URL 映射：username -> url
        self._avatar_urls = {}
        # 数据库字段名（自省后确定）
        self._avatar_column = None
        self._mute_column = None

    # ------------------------------------------------------------------
    # 数据库字段自省
    # ------------------------------------------------------------------
    def introspect_contact_schema(self, conn: sqlite3.Connection) -> None:
        """自省 contact 表结构，确定头像 URL 列名与免打扰列名

        Args:
            conn: 已解密的 contact.db 连接
        """
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(contact)")
            columns = {row[1] for row in cursor.fetchall()}

            # 头像列
            for col in _AVATAR_URL_COLUMNS:
                if col in columns:
                    self._avatar_column = col
                    logger.info(f"头像字段识别为：{col}")
                    break

            # 免打扰列（contact 表层面）
            for col in _MUTE_COLUMNS:
                if col in columns:
                    self._mute_column = col
                    logger.info(f"联系人免打扰字段识别为：{col}")
                    break

            if not self._avatar_column:
                logger.warning(f"未在 contact 表中找到头像字段，可用列：{sorted(columns)[:30]}")
        except Exception as e:
            logger.error(f"自省 contact 表结构失败：{e}")

    @property
    def avatar_column(self) -> Optional[str]:
        return self._avatar_column

    @property
    def mute_column(self) -> Optional[str]:
        return self._mute_column

    # ------------------------------------------------------------------
    # URL 映射维护
    # ------------------------------------------------------------------
    def set_avatar_url(self, username: str, url: str) -> None:
        """记录联系人的头像 URL"""
        if username and url:
            self._avatar_urls[username] = url

    def get_avatar_url(self, username: str) -> Optional[str]:
        return self._avatar_urls.get(username)

    # ------------------------------------------------------------------
    # 本地路径获取
    # ------------------------------------------------------------------
    def _lock_for(self, username: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(username)
            if lock is None:
                lock = threading.Lock()
                self._locks[username] = lock
            return lock

    def _cache_path(self, username: str) -> str:
        # 用 username 的安全形式作为文件名（去除特殊字符）
        safe = "".join(c if c.isalnum() else "_" for c in username)
        return os.path.join(self.cache_dir, f"{safe}.png")

    def get_avatar_path(self, username: str) -> Optional[str]:
        """获取联系人/群头像的本地路径

        优先返回缓存；若未缓存且有 URL，则同步下载（带超时）。
        下载失败或无 URL 时返回默认图标（若有）。
        """
        if not username:
            return self.default_icon

        # 命中内存缓存
        if username in self._path_cache:
            path = self._path_cache[username]
            if path and os.path.exists(path):
                return path

        lock = self._lock_for(username)
        if not lock.acquire(blocking=False):
            # 正在下载中，先用默认图标
            return self.default_icon

        try:
            target = self._cache_path(username)

            # 磁盘缓存有效则直接使用
            if os.path.exists(target):
                mtime = os.path.getmtime(target)
                if time.time() - mtime < _CACHE_TTL:
                    self._path_cache[username] = target
                    return target

            # 失败冷却期内不重试
            fail_ts = self._fail_map.get(username)
            if fail_ts and time.time() - fail_ts < _FAIL_COOLDOWN:
                return self.default_icon

            url = self._avatar_urls.get(username)
            if not url:
                # 没有 URL，使用默认图标
                return self.default_icon

            # 同步下载
            ok = self._download_and_save(url, target)
            if ok:
                self._path_cache[username] = target
                self._fail_map.pop(username, None)
                return target
            else:
                self._fail_map[username] = time.time()
                return self.default_icon
        finally:
            lock.release()

    def _download_and_save(self, url: str, target: str) -> bool:
        """下载头像并保存为规整的 PNG"""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://wx.qlogo.cn/",
                },
            )
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
                raw = resp.read()

            if not raw or len(raw) < 100:
                logger.debug(f"头像数据过小，跳过：{url}")
                return False

            # 用 Pillow 规整为正方形 PNG
            img = self._normalize_image(raw)
            if img is None:
                return False

            img.save(target, "PNG")
            logger.debug(f"头像已缓存：{target}")
            return True
        except urllib.error.URLError as e:
            logger.debug(f"头像下载失败（网络）：{e}")
            return False
        except Exception as e:
            logger.debug(f"头像下载失败：{e}")
            return False

    @staticmethod
    def _normalize_image(raw: bytes):
        """将原始图片字节规整为正方形 PNG"""
        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow 未安装，无法处理头像")
            return None

        try:
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception as e:
            logger.debug(f"头像图片解析失败：{e}")
            return None

        # 居中裁剪为正方形后缩放
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((_AVATAR_SIZE, _AVATAR_SIZE), Image.LANCZOS)
        return img

    def preload_async(self, usernames: list) -> None:
        """在后台线程预下载一批联系人头像（不阻塞主流程）"""
        targets = [u for u in usernames if u and u not in self._path_cache]
        if not targets:
            return

        def _worker():
            for u in targets[:200]:  # 限制预下载数量，避免启动过慢
                try:
                    self.get_avatar_path(u)
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True, name="avatar-preload")
        t.start()
