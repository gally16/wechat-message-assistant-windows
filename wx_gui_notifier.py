import sys
import os
import time
import sqlite3
import logging
import threading
import ctypes
from datetime import datetime
from collections import deque

# 导入 GUI 配置管理模块
from utils.gui_config import ensure_config_file, validate_keys_file, get_gui_config, CONFIG_FILE

# PyQt5 & QFluentWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QSize, pyqtSlot
from PyQt5.QtGui import QIcon, QFont, QColor
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
    QSystemTrayIcon, QMenu, QAction, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton
)

from qfluentwidgets import (
    FluentWidget, NavigationItemPosition, MessageBox, 
    SettingCardGroup, PushSettingCard, SwitchSettingCard, 
    InfoBar, InfoBarPosition, 
    ComboBoxSettingCard, TitleLabel, SubtitleLabel,
    BodyLabel, TextEdit, SettingCard,
    SpinBox, ProgressBar
)
# 尝试导入 SpinBoxSettingCard，如果失败则使用自定义
try:
    from qfluentwidgets import SpinBoxSettingCard
    HAS_SPINBOX_CARD = True
except ImportError:
    HAS_SPINBOX_CARD = False

from qfluentwidgets.common.icon import FluentIcon as FIF

# 业务逻辑依赖
from core.wx_decrypt import get_wx_info, HAS_DECRYPT
from core.wechat_decrypt_core import full_decrypt, decrypt_wal_full
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 头像下载与缓存
from utils.avatar_cache import AvatarCache

# 使用 winotify 实现 Windows 通知
try:
    from winotify import Notification, audio
    HAS_WINOTIFY = True
except ImportError:
    HAS_WINOTIFY = False

if not HAS_DECRYPT:
    print("警告：wechat-decrypt 模块不可用，请先运行密钥提取")

# wechat-decrypt 常量（用于快速解密）
PAGE_SZ = 4096
RESERVE_SZ = 80
KEY_SZ = 32
SALT_SZ = 16
SQLITE_HDR = b'SQLite format 3\x00'
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24

# --- 全局配置与日志 ---
LOG_STREAM = deque(maxlen=100)

class StreamLogger(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        LOG_STREAM.append(msg)

logger = logging.getLogger("WeChatNotifier")
logger.setLevel(logging.INFO)

# 检查是否已经添加过处理器，避免重复
if not logger.handlers:
    stream_handler = StreamLogger()
    stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(stream_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)

# --- 自定义设置卡片 (替代 SliderSettingCard) ---
class SliderSettingCard(SettingCard):
    """自定义设置卡片，确保与路径选择按钮右侧对齐"""
    def __init__(self, min_val, max_val, step, default, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        
        # 存储值的接口 (模拟 ConfigItem 行为，供外部读取)
        self._value = default
        
        # 创建水平布局
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(16, 16, 16, 16)
        
        # 添加弹性空间，将微调框推到右侧
        h_layout.addStretch()
        
        # 添加微调框
        self.spin_box = SpinBox()
        self.spin_box.setRange(min_val, max_val)
        self.spin_box.setSingleStep(step)
        self.spin_box.setValue(default)
        # 确保微调框可编辑
        self.spin_box.setReadOnly(False)
        self.spin_box.setFixedWidth(150)
        
        h_layout.addWidget(self.spin_box)
        
        # 连接值变化信号
        self.spin_box.valueChanged.connect(self._on_value_changed)
        
        # 添加到卡片布局
        self.layout().addLayout(h_layout)
        
    def _on_value_changed(self, value):
        self._value = value
        
    @property
    def value(self):
        return self._value

# --- 后端逻辑线程 ---

class WeChatMonitorWorker(QObject):
    """后台工作线程"""
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    msg_count_signal = pyqtSignal(int)
    last_msg_signal = pyqtSignal(str)
    finished = pyqtSignal()
    notify_signal = pyqtSignal(str, str)  # title, message
    contact_map_signal = pyqtSignal(object)  # {username: display_name}
    notified_contact_signal = pyqtSignal(str, str)  # username, display_name

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = False
        self.observer = None
        self.last_local_id = 0
        self.contact_map = {}
        self.wx_info = None
        self.key = None
        self.db_path = None
        self.micro_db_path = None
        self.session_db_path = None
        
        self.temp_dir = config.get('temp_dir', './temp_data')
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        self.db_backup = os.path.join(self.temp_dir, "msg_decrypted.db")
        self.micro_backup = os.path.join(self.temp_dir, "micro_decrypted.db")
        
        # 用于跟踪 session 状态
        self.prev_session_state = {}
        
        # 全局缓存的已解密 session.db 路径
        self.session_decrypted_path = os.path.join(self.temp_dir, "session_cache.db")

        # 过滤配置（默认开启，避免免打扰/公众号文章打扰）
        self.filter_mute = config.get('filter_mute', True)
        self.filter_official_article = config.get('filter_official_article', True)
        self.manual_mute_set = set(config.get('mute_usernames', []) or [])
        self.enable_notification_sound = config.get('enable_notification_sound', True)
        self.sound_alias = config.get('sound_alias', 'SystemAsterisk')
        self.keys_file = config.get('keys_file')
        self.all_keys = {}

        # 默认微信图标路径
        self.default_icon = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.png')

        # 头像缓存管理器
        self.avatar_cache = AvatarCache(self.temp_dir, default_icon=self.default_icon)

        # SessionTable 免打扰列名（自省后确定），None 表示该表没有免打扰字段
        self.session_mute_column = None
        # contact 表层面的免打扰列名（自省后确定）
        self.contact_mute_column = None
        # contact 表层面的免打扰状态：username -> 是否免打扰
        self.contact_mute_map = {}

        # 公众号用户名前缀与 PC 聚合会话名
        self._official_prefix = 'gh_'
        self._official_holders = {
            'brandsessionholder',
            'officialaccounts',
            'mphelper',
        }

    def _candidate_keys_files(self):
        """按优先级查找 all_keys.json，优先使用 GUI 配置里的绝对路径。"""
        candidates = []

        def add(path):
            if not path:
                return
            if not os.path.isabs(path):
                bases = [
                    os.getcwd(),
                    os.path.dirname(__file__),
                    os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else '',
                ]
                for base in bases:
                    if base:
                        candidates.append(os.path.abspath(os.path.join(base, path)))
            else:
                candidates.append(os.path.abspath(path))

        add(self.keys_file)

        try:
            if os.path.exists(CONFIG_FILE):
                import json
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                add(cfg.get('keys_file'))
        except Exception:
            pass

        exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else ''
        for path in [
            os.path.join(exe_dir, 'all_keys.json') if exe_dir else '',
            os.path.join(os.getcwd(), 'all_keys.json'),
            os.path.join(os.path.dirname(__file__), 'all_keys.json'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'all_keys.json'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'all_keys.json'),
        ]:
            add(path)

        seen = set()
        unique = []
        for path in candidates:
            norm = os.path.normcase(os.path.abspath(path))
            if norm not in seen:
                seen.add(norm)
                unique.append(path)
        return unique

    def _load_all_keys(self):
        """加载 all_keys.json；失败时返回空字典，不再误用 message key。"""
        import json
        for path in self._candidate_keys_files():
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    self.keys_file = path
                    self.all_keys = data
                    self.log(f"已加载 all_keys.json：{path}")
                    return data
            except Exception as e:
                self.log(f"读取 all_keys.json 失败：{path}，{e}")
        self.all_keys = {}
        return {}

    @staticmethod
    def _get_db_key(all_keys, rel_path):
        """兼容不同路径分隔符，从 all_keys 中取数据库专用密钥。"""
        target = rel_path.replace('/', '\\').lower()
        for key, info in (all_keys or {}).items():
            if key.replace('/', '\\').lower() == target and isinstance(info, dict):
                return info.get('enc_key')
        return None

    def log(self, msg):
        logger.info(msg)
        self.log_signal.emit(msg)

    def init_wx_env(self):
        try:
            info_list = get_wx_info()
            if not info_list:
                raise Exception("未检测到已登录的微信进程")
            
            self.wx_info = info_list[0]
            self.key = self.wx_info.get('key')
            self.db_path = self.wx_info.get('msg_path')
            self.micro_db_path = self.wx_info.get('micro_path')
            self.micro_key = self.wx_info.get('micro_key', self.key)  # 使用 micro.db 专用密钥
            
            # 计算 session.db 路径
            wx_dir = os.path.dirname(os.path.dirname(self.db_path))
            self.session_db_path = os.path.join(wx_dir, "session", "session.db")
            
            # 从 all_keys.json 获取各数据库专用密钥。不能用 message key 解 contact/session。
            all_keys = self._load_all_keys()
            if not all_keys:
                checked = "\n".join(f"  - {p}" for p in self._candidate_keys_files())
                raise Exception(f"未找到有效 all_keys.json，已检查：\n{checked}")

            message_key = self._get_db_key(all_keys, 'message\\message_0.db')
            contact_key = self._get_db_key(all_keys, 'contact\\contact.db')
            session_key = self._get_db_key(all_keys, 'session\\session.db')

            if message_key:
                self.key = message_key
            if not contact_key:
                raise Exception("all_keys.json 缺少 contact\\contact.db 的 enc_key，无法加载联系人")
            if not session_key:
                raise Exception("all_keys.json 缺少 session\\session.db 的 enc_key，无法监听会话")

            self.micro_key = contact_key
            self.session_key = session_key
            self.log(f"message_0.db 密钥：{self.key[:20]}...")
            self.log(f"contact.db 密钥：{self.micro_key[:20]}...")
            self.log(f"session.db 密钥：{self.session_key[:20]}...")
            
            if not self.key or not self.db_path:
                raise Exception("获取密钥或路径失败")
            
            self.log(f"微信环境初始化成功：{self.wx_info.get('wxid')}")
            return True
        except Exception as e:
            self.log(f"初始化失败：{str(e)}")
            return False

    def load_contacts(self):
        if not self.micro_db_path or not os.path.exists(self.micro_db_path):
            self.log("micro.db 不存在，跳过联系人加载")
            return
        try:
            # 使用 wx_decrypt_core 模块解密（避免 monitor_web 的模块级别配置加载）
            # 使用 micro.db 的专用密钥解密
            self.log(f"开始解密 micro.db...")
            self.log(f"  源文件：{self.micro_db_path}")
            self.log(f"  目标文件：{self.micro_backup}")
            self.log(f"  密钥：{self.micro_key[:20]}...")
            
            pages, ms = full_decrypt(self.micro_db_path, self.micro_backup, bytes.fromhex(self.micro_key))
            self.log(f"micro.db 解密完成：{pages}页/{ms:.0f}ms")
            
            # Patch WAL（如果有）- 必须在查询前 patch
            wal_path = self.micro_db_path + "-wal"
            if os.path.exists(wal_path):
                wal_patched, wal_ms = decrypt_wal_full(wal_path, self.micro_backup, bytes.fromhex(self.micro_key))
                self.log(f"micro.db WAL patch: {wal_patched}页/{wal_ms:.0f}ms")
            else:
                self.log("micro.db 无 WAL 文件")
            
            # 验证解密后的文件
            if os.path.exists(self.micro_backup):
                file_size = os.path.getsize(self.micro_backup)
                self.log(f"解密后的文件大小：{file_size} bytes")
                
                try:
                    test_conn = sqlite3.connect(f"file:{self.micro_backup}?mode=ro", uri=True)
                    test_cursor = test_conn.cursor()
                    test_cursor.execute("SELECT 1 FROM sqlite_master LIMIT 1")
                    # 检查是否有 contact 表
                    test_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contact'")
                    table_exists = test_cursor.fetchone() is not None
                    test_conn.close()
                    if table_exists:
                        self.log("✅ micro.db 解密验证成功，contact 表存在")
                    else:
                        self.log("⚠️ micro.db 解密成功，但未找到 contact 表")
                        # 列出所有表
                        test_conn = sqlite3.connect(f"file:{self.micro_backup}?mode=ro", uri=True)
                        test_cursor = test_conn.cursor()
                        tables = test_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                        test_conn.close()
                        self.log(f"  可用表：{[t[0] for t in tables[:10]]}")
                        if len(tables) > 10:
                            self.log(f"  ... 还有 {len(tables) - 10} 个表")
                except Exception as verify_err:
                    self.log(f"❌ micro.db 解密验证失败：{verify_err}")
                    # 尝试删除损坏的文件
                    try:
                        os.remove(self.micro_backup)
                    except:
                        pass
                    return
            
            conn = sqlite3.connect(self.micro_backup)
            cursor = conn.cursor()
            
            # 微信 4.x: 加载联系人
            # 先检查表结构
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='contact'")
            table_info = cursor.fetchone()
            if table_info:
                self.log(f"contact 表结构：{table_info[0][:200]}")
            else:
                self.log("⚠️ 未找到 contact 表")
            
            # 自省 contact 表结构，识别头像字段与免打扰字段
            self.avatar_cache.introspect_contact_schema(conn)
            self.contact_mute_column = self.avatar_cache.mute_column
            avatar_col = self.avatar_cache.avatar_column

            # 构建查询：基础字段 + 头像字段 + 免打扰字段（如存在）
            select_cols = ["username", "nick_name", "remark"]
            if avatar_col and avatar_col not in select_cols:
                select_cols.append(avatar_col)
            if self.contact_mute_column and self.contact_mute_column not in select_cols:
                select_cols.append(self.contact_mute_column)

            select_sql = f"SELECT {', '.join(select_cols)} FROM contact WHERE username IS NOT NULL;"
            cursor.execute(select_sql)

            avatar_loaded = 0
            mute_loaded = 0
            for row in cursor.fetchall():
                values = dict(zip(select_cols, row))
                uname = values.get("username")
                nick = values.get("nick_name")
                remark = values.get("remark")
                # 优先使用备注名，其次使用昵称
                name = remark if remark else nick
                if name and uname:
                    # 存储 username -> name 的映射
                    self.contact_map[uname] = name

                # 头像 URL
                if avatar_col and uname:
                    avatar_url = values.get(avatar_col)
                    if isinstance(avatar_url, bytes):
                        try:
                            avatar_url = avatar_url.decode('utf-8', errors='replace').strip()
                        except Exception:
                            avatar_url = None
                    if avatar_url and avatar_url.startswith(('http://', 'https://')):
                        self.avatar_cache.set_avatar_url(uname, avatar_url)
                        avatar_loaded += 1

                # 联系人层面的免打扰状态
                if self.contact_mute_column and uname:
                    mute_val = values.get(self.contact_mute_column)
                    is_muted = self._is_mute_value(self.contact_mute_column, mute_val)
                    self.contact_mute_map[uname] = is_muted
                    if is_muted:
                        mute_loaded += 1

            # 保存 cursor 用于后续查询
            self.contact_cursor = cursor
            self.contact_conn = conn
            
            self.log(f"✓ 联系人加载完成：{len(self.contact_map)} 个")
            self.log(f"   头像 URL：{avatar_loaded} 个 | 免打扰联系人：{mute_loaded} 个")
            self.contact_map_signal.emit(dict(self.contact_map))

            # 后台预下载前若干个联系人的头像，提升首条消息的显示效果
            self.avatar_cache.preload_async(list(self.contact_map.keys())[:80])
            
            # 打印前 5 个联系人用于调试
            for i, (uname, name) in enumerate(list(self.contact_map.items())[:5]):
                self.log(f"  [{i+1}] {uname} -> {name}")
            if len(self.contact_map) > 5:
                self.log(f"  ... 还有 {len(self.contact_map) - 5} 个联系人")
            
            # 尝试读取当前用户的昵称并保存到配置文件
            self._save_current_user_nickname()
        
        except Exception as e:
            self.log(f"加载联系人失败：{str(e)}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
        
        # 无论如何都要初始化 session 状态（避免历史消息重复推送）
        # 即使联系人加载失败，也要初始化 session 状态
        self.init_session_state()
    
    def _save_current_user_nickname(self):
        """读取当前用户的昵称并保存到配置文件"""
        try:
            if not self.contact_cursor or not self.wx_info:
                return
            
            current_wxid = self.wx_info.get('wxid', '')
            if not current_wxid:
                return
            
            # 查询当前用户的昵称
            self.contact_cursor.execute(
                "SELECT nick_name, remark FROM contact WHERE username=?",
                (current_wxid,)
            )
            row = self.contact_cursor.fetchone()
            
            if row:
                nick_name, remark = row
                # 优先使用备注名，其次使用昵称
                nickname = remark if remark else nick_name
                
                if nickname:
                    # 更新配置文件
                    from utils.gui_config import save_config
                    self.config['nickname'] = nickname
                    save_config(self.config)
                    self.log(f"✓ 已保存用户昵称：{nickname}")
        except Exception as e:
            self.log(f"读取用户昵称失败：{e}")
    
    def init_session_state(self):
        """初始化 session 状态，避免历史消息重复推送（参考 monitor_web.py line 1366-1377）"""
        if not self.session_db_path or not os.path.exists(self.session_db_path):
            self.log("session.db 不存在，跳过初始化")
            return
        
        try:
            # 使用 session.db 的专用密钥
            if isinstance(self.session_key, str):
                enc_key = bytes.fromhex(self.session_key)
            else:
                enc_key = self.session_key
            
            # 1. 初始全量解密（参考 monitor_web.py line 1367）
            session_backup = os.path.join(os.path.dirname(self.db_backup), "session_init.db")
            pages, ms = full_decrypt(self.session_db_path, session_backup, enc_key)
            self.log(f"初始解密 session.db: {pages}页/{ms:.0f}ms")
            
            # 2. Patch WAL（参考 monitor_web.py line 1370-1372）
            wal_path = self.session_db_path + "-wal"
            wal_patched = 0
            wal_ms = 0
            if os.path.exists(wal_path):
                wal_patched, wal_ms = decrypt_wal_full(wal_path, session_backup, enc_key)
                self.log(f"初始 WAL patch: {wal_patched}页/{wal_ms:.0f}ms")
            else:
                self.log("未发现 WAL 文件")
            
            # 3. 查询当前状态（参考 monitor_web.py line 1376）
            conn = sqlite3.connect(session_backup)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT username, last_timestamp, last_msg_type
                FROM SessionTable WHERE last_timestamp > 0
            """)
            
            for row in cursor.fetchall():
                username, timestamp, msg_type = row
                self.prev_session_state[username] = {
                    'timestamp': timestamp,
                    'msg_type': msg_type,
                }
            
            conn.close()
            
            # 4. 清理临时文件
            try:
                if os.path.exists(session_backup):
                    os.remove(session_backup)
                if os.path.exists(session_backup + "-wal"):
                    os.remove(session_backup + "-wal")
                if os.path.exists(session_backup + "-shm"):
                    os.remove(session_backup + "-shm")
            except:
                pass
            
            self.log(f"✓ 已初始化 session 状态，跟踪 {len(self.prev_session_state)} 个会话")
            # 打印前 5 个会话的详细信息用于调试
            for i, (username, state) in enumerate(list(self.prev_session_state.items())[:5]):
                self.log(f"  [{i+1}] {username}: timestamp={state['timestamp']}, type={state['msg_type']}")
            if len(self.prev_session_state) > 5:
                self.log(f"  ... 还有 {len(self.prev_session_state) - 5} 个会话")
        
        except Exception as e:
            self.log(f"初始化 session 状态失败：{str(e)}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")

    def decrypt_msg_db(self):
        if not os.path.exists(self.db_path):
            return False
        try:
            decrypt(self.key, self.db_path, self.db_backup)
            return True
        except Exception as e:
            self.log(f"解密失败：{str(e)}")
            return False

    def process_messages(self):
        """从 session.db 获取新消息（速度快，准确）"""
        if not self.session_db_path or not os.path.exists(self.session_db_path):
            self.log("session.db 不存在")
            return
        
        try:
            # 解密 session.db
            from core.wx_decrypt import decrypt as wx_decrypt
            session_backup = os.path.join(os.path.dirname(self.db_backup), "session_decrypted.db")
            wx_decrypt(self.key, self.session_db_path, session_backup)
            
            # 处理 WAL
            wal_path = self.session_db_path + "-wal"
            if os.path.exists(wal_path):
                try:
                    conn_wal = sqlite3.connect(session_backup)
                    conn_wal.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn_wal.close()
                except:
                    pass
            
            # 查询 session 状态
            conn = sqlite3.connect(session_backup)
            cursor = conn.cursor()

            # 自省免打扰字段
            self._detect_session_mute_column(conn)
            mute_col = self.session_mute_column

            base_cols = ["username", "unread_count", "summary", "last_timestamp",
                         "last_msg_type", "last_msg_sender", "last_sender_display_name"]
            select_cols = list(base_cols)
            if mute_col and mute_col not in select_cols:
                select_cols.append(mute_col)
            select_sql = f"SELECT {', '.join(select_cols)} FROM SessionTable WHERE last_timestamp > 0"
            cursor.execute(select_sql)
            
            total_count = 0
            for row in cursor.fetchall():
                values = dict(zip(select_cols, row))
                username = values["username"]
                summary = values["summary"]
                timestamp = values["last_timestamp"]
                msg_type = values["last_msg_type"]
                sender = values["last_msg_sender"]
                sender_name = values["last_sender_display_name"]
                
                # 检查是否是新消息
                if username in self.prev_session_state:
                    prev = self.prev_session_state[username]
                    if timestamp <= prev['timestamp']:
                        continue

                # 过滤：免打扰 / 公众号文章
                is_muted = False
                if mute_col:
                    is_muted = self._is_session_muted(values.get(mute_col))
                if not is_muted and username in self.contact_mute_map:
                    is_muted = self.contact_mute_map[username]
                if not is_muted and username in self.manual_mute_set:
                    is_muted = True
                should_skip, reason = self._should_skip(
                    username, msg_type, is_muted,
                    sender=sender,
                    display_name=sender_name or self.contact_map.get(username, username)
                )
                if should_skip:
                    self.prev_session_state[username] = {
                        'timestamp': timestamp,
                        'msg_type': msg_type,
                    }
                    continue
                
                # 获取聊天显示名称
                display = self.contact_map.get(username, username)
                is_group = '@chatroom' in username
                
                # 获取发送者
                if is_group:
                    # 群聊：使用 last_sender_display_name 或从联系人查找
                    final_sender = sender_name or self.contact_map.get(sender, sender)
                else:
                    # 单聊：直接使用聊天名称
                    final_sender = display
                
                # 解析消息内容
                if isinstance(summary, bytes):
                    try:
                        summary = summary.decode('utf-8', errors='replace')
                    except:
                        summary = '(压缩内容)'
                
                # 群消息格式：wxid_xxx:\n内容，提取内容
                if summary and ':\n' in summary:
                    summary = summary.split(':\n', 1)[1]
                
                # 根据消息类型显示
                msg_text = self.format_message_content(summary, msg_type)
                
                # 发送通知
                if final_sender and final_sender != display:
                    notification_text = f"{final_sender}: {msg_text}"
                else:
                    notification_text = msg_text
                
                avatar_path = self.avatar_cache.get_avatar_path(username)
                self.send_notification(display, notification_text, timestamp,
                                       icon_path=avatar_path, username=username)
                self.notified_contact_signal.emit(username, display)
                total_count += 1
                
                # 更新状态
                self.prev_session_state[username] = {
                    'timestamp': timestamp,
                    'msg_type': msg_type,
                }
            
            conn.close()
            
            # 清理临时文件
            try:
                if os.path.exists(session_backup):
                    os.remove(session_backup)
                if os.path.exists(session_backup + "-wal"):
                    os.remove(session_backup + "-wal")
                if os.path.exists(session_backup + "-shm"):
                    os.remove(session_backup + "-shm")
            except:
                pass
            
            if total_count > 0:
                self.log(f"处理了 {total_count} 条新消息")
        
        except Exception as e:
            self.log(f"处理消息失败：{str(e)}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
    
    def run_polling(self):
        """主动轮询模式（使用 wx_decrypt_core 模块）"""
        if not self.session_db_path or not os.path.exists(self.session_db_path):
            self.log("session.db 不存在")
            return
        
        wal_path = self.session_db_path + "-wal"
        
        # 使用 session.db 的专用密钥
        if isinstance(self.session_key, str):
            enc_key = bytes.fromhex(self.session_key)
        else:
            enc_key = self.session_key
        
        # 初始全量解密 + WAL patch
        self.log("🚀 初始解密 session.db...")
        t0 = time.time()
        try:
            pages, ms = full_decrypt(self.session_db_path, self.session_decrypted_path, enc_key)
            self.log(f"✅ full_decrypt 返回：{pages}页，{ms:.1f}ms")
            
            wal_patched, wal_ms = decrypt_wal_full(wal_path, self.session_decrypted_path, enc_key)
            t1 = time.time()
            self.log(f"✅ 初始解密完成：{(t1-t0)*1000:.1f}ms, WAL patch {wal_patched}页")
            
            # 验证解密后的文件
            if os.path.exists(self.session_decrypted_path):
                sz = os.path.getsize(self.session_decrypted_path)
                self.log(f"📁 解密后的文件大小：{sz} bytes")
                
                # 尝试用 SQLite 验证
                try:
                    test_conn = sqlite3.connect(f"file:{self.session_decrypted_path}?mode=ro", uri=True)
                    test_conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
                    test_conn.close()
                    self.log("✅ 解密后的数据库验证成功")
                except Exception as verify_err:
                    self.log(f"❌ 解密后的数据库验证失败：{verify_err}")
        except Exception as e:
            self.log(f"❌ 初始解密失败：{e}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
            return
        
        # 立即查询并推送第一次（参考 monitor_web.py check_updates）
        self.log("🔄 执行第一次 process_and_push...")
        try:
            self.process_and_push()
            self.log("✅ 第一次 process_and_push 完成")
        except Exception as first_err:
            self.log(f"❌ 第一次 process_and_push 失败：{first_err}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
        
        prev_wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
        prev_db_mtime = os.path.getmtime(self.session_db_path)
        
        # 轮询间隔 30ms
        poll_interval = 0.03
        self.log(f"轮询间隔：{poll_interval*1000:.0f}ms")
        
        poll_count = 0
        last_log_time = time.time()
        
        self.log("开始轮询循环...")
        while self.running:
            time.sleep(poll_interval)
            poll_count += 1
            
            try:
                # 检测文件变化
                wal_mtime = os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0
                db_mtime = os.path.getmtime(self.session_db_path)
                
                if wal_mtime == prev_wal_mtime and db_mtime == prev_db_mtime:
                    continue
                
                # 文件有变化，立即解密 + 推送（参考 monitor_web.py，零延迟）
                self.log(f"检测到文件变化，开始解密... (第 {poll_count} 次)")
                t_start = time.time()
                
                # 1. 解密主数据库
                try:
                    pages, ms = full_decrypt(self.session_db_path, self.session_decrypted_path, enc_key)
                    self.log(f"解密完成：{pages}页，{ms:.1f}ms")
                except Exception as decrypt_err:
                    self.log(f"解密失败：{decrypt_err}")
                    import traceback
                    self.log(traceback.format_exc())
                    continue
                
                # 2. patch WAL
                try:
                    wal_patched, wal_ms = decrypt_wal_full(wal_path, self.session_decrypted_path, enc_key)
                    self.log(f"WAL patch: {wal_patched}页")
                except Exception as wal_err:
                    self.log(f"WAL patch 失败：{wal_err}")
                    import traceback
                    self.log(traceback.format_exc())
                    continue
                
                # 3. 立即查询并推送
                try:
                    self.process_and_push()
                except Exception as push_err:
                    self.log(f"推送失败：{push_err}")
                    import traceback
                    self.log(traceback.format_exc())
                    # 继续执行，不中断
                
                t_end = time.time()
                
                # 每 5 秒打印一次状态
                if time.time() - last_log_time > 5:
                    elapsed = (t_end - t_start) * 1000
                    self.log(f"轮询：{poll_count} 次，解密 + 推送耗时 {elapsed:.1f}ms, WAL {wal_patched}页")
                    last_log_time = time.time()
                
                prev_wal_mtime = wal_mtime
                prev_db_mtime = db_mtime
                
            except Exception as e:
                self.log(f"轮询错误：{str(e)}")
                import traceback
                self.log(traceback.format_exc())
                time.sleep(0.1)
        
        self.log(f"轮询结束，共 {poll_count} 次")
    
    def _detect_session_mute_column(self, conn):
        """自省 SessionTable 结构，识别免打扰列名（仅在首次调用时执行）

        微信 4.x 的 SessionTable 通常含 mute_notification 列：
            0 = 正常提醒，1 = 消息免打扰
        不同小版本字段名可能不同，这里依次尝试。
        """
        if self.session_mute_column is not None:
            return
        candidates = [
            "mute_notification", "is_mute", "mute", "notification_on", "is_muted",
            "notify_status", "notifyflag", "notify_flag", "chatroom_notify",
            "chatroomnotify", "message_notice",
        ]
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(SessionTable)")
            columns = {row[1] for row in cursor.fetchall()}
            for col in candidates:
                if col in columns:
                    self.session_mute_column = col
                    self.log(f"📌 SessionTable 免打扰字段识别为：{col}")
                    return
            self.session_mute_column = ""  # 标记为已检测但无此字段
            self.log("ℹ️ SessionTable 未发现免打扰字段，将仅依赖联系人层面免打扰状态")
        except Exception as e:
            self.log(f"自省 SessionTable 结构失败：{e}")
            self.session_mute_column = ""

    def _is_mute_value(self, column_name, value) -> bool:
        """统一判断免打扰字段值，兼容正向/反向语义字段。"""
        if value is None:
            return False

        name = (column_name or "").strip().lower()
        text = str(value).strip().lower()
        falsy = {"0", "false", "no", "off", "", "none", "null"}
        truthy = {"1", "true", "yes", "on", "mute", "muted"}

        # 这些字段一般表示“是否开启通知”：0/false/off 才是免打扰。
        if name in {"notification_on", "message_notice", "notify_status"}:
            return text in falsy

        return text in truthy

    def _is_session_muted(self, mute_val) -> bool:
        """根据 SessionTable 免打扰字段值判断是否免打扰"""
        return self._is_mute_value(self.session_mute_column, mute_val)

    def _is_official_account(self, username=None, sender=None, display_name=None) -> bool:
        """判断是否为公众号或公众号聚合会话。"""
        values = [username, sender, display_name]
        lowered = [(v or "").strip().lower() for v in values]

        if any(v in self._official_holders for v in lowered):
            return True
        if any(v.startswith(self._official_prefix) for v in lowered):
            return True

        return False

    def _is_manually_muted(self, username=None, sender=None, display_name=None) -> bool:
        """手动过滤支持 username、群名/昵称、发送者显示名。"""
        manual = {str(v).strip().lower() for v in self.manual_mute_set if str(v).strip()}
        values = [(v or "").strip().lower() for v in (username, sender, display_name)]
        return any(v in manual for v in values if v)

    def _should_skip(self, username, msg_type, is_muted, sender=None, display_name=None):
        """根据过滤配置判断是否应跳过该消息的通知"""
        # 0. 用户手动配置的联系人/群始终过滤，不依赖数据库免打扰字段。
        if self._is_manually_muted(username, sender, display_name):
            return True, "手动过滤"

        # 1. 免打扰过滤
        if self.filter_mute and is_muted:
            return True, "免打扰"

        try:
            mt = int(msg_type)
        except Exception:
            mt = -1

        # 2. 公众号文章推送过滤（公众号 + 富媒体/链接消息 msg_type==49）
        if self.filter_official_article and self._is_official_account(username, sender, display_name):
            # brandsessionholder 是微信 PC 的公众号聚合会话，直接过滤。
            if (username or "").strip().lower() in self._official_holders:
                return True, "公众号"
            if mt in (49, 1):
                return True, "公众号"
        return False, None

    def process_and_push(self):
        """查询并推送（完全参考 monitor_web.py，零延迟）

        相比原版增加：
        1. 过滤已设置消息免打扰的联系人/群
        2. 过滤公众号文章推送
        3. 通知中显示联系人/群头像
        """
        if not os.path.exists(self.session_decrypted_path):
            self.log(f"session 解密文件不存在：{self.session_decrypted_path}")
            return
        
        try:
            # 使用只读模式查询（避免锁冲突）
            conn = sqlite3.connect(f"file:{self.session_decrypted_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # 首次自省 SessionTable 免打扰字段
            self._detect_session_mute_column(conn)

            # 构建查询：如存在免打扰字段则一并查出
            base_cols = ["username", "unread_count", "summary", "last_timestamp",
                         "last_msg_type", "last_msg_sender", "last_sender_display_name"]
            mute_col = self.session_mute_column
            select_cols = list(base_cols)
            if mute_col and mute_col not in select_cols:
                select_cols.append(mute_col)

            select_sql = f"SELECT {', '.join(select_cols)} FROM SessionTable WHERE last_timestamp > 0"
            cursor.execute(select_sql)
            rows = cursor.fetchall()
            conn.close()
            self.log(f"📊 查询到 {len(rows)} 个会话")
            
            # 收集所有新消息
            new_msgs = []
            check_count = 0
            new_count = 0
            skip_count = 0
            filter_mute_count = 0
            filter_official_count = 0
            filter_manual_count = 0
            
            for row in rows:
                values = dict(zip(select_cols, row))
                username = values["username"]
                unread = values["unread_count"]
                summary = values["summary"]
                timestamp = values["last_timestamp"]
                msg_type = values["last_msg_type"]
                sender = values["last_msg_sender"]
                sender_name = values["last_sender_display_name"]
                check_count += 1
                
                # 检查是否是新消息
                if username in self.prev_session_state:
                    prev = self.prev_session_state[username]
                    if timestamp <= prev['timestamp']:
                        skip_count += 1
                        continue

                # ---- 过滤：免打扰 / 公众号文章 ----
                # 优先使用 SessionTable 的免打扰字段，其次使用 contact 表的免打扰状态
                is_muted = False
                if mute_col:
                    is_muted = self._is_session_muted(values.get(mute_col))
                if not is_muted and username in self.contact_mute_map:
                    is_muted = self.contact_mute_map[username]
                if not is_muted and username in self.manual_mute_set:
                    is_muted = True

                should_skip, reason = self._should_skip(
                    username, msg_type, is_muted,
                    sender=sender,
                    display_name=sender_name or self.contact_map.get(username, username)
                )
                if should_skip:
                    if reason == "手动过滤":
                        filter_manual_count += 1
                        self.log(f"🚫 [手动过滤] {username} (msg_type={msg_type})")
                    elif reason == "免打扰":
                        filter_mute_count += 1
                        self.log(f"🔕 [免打扰过滤] {username} (msg_type={msg_type})")
                    else:
                        filter_official_count += 1
                        self.log(f"📰 [公众号过滤] {username} (msg_type={msg_type})")
                    # 仍需更新状态，避免取消免打扰后一次性补推历史消息
                    self.prev_session_state[username] = {
                        'timestamp': timestamp,
                        'msg_type': msg_type,
                    }
                    continue
                
                # 记录新消息
                new_count += 1
                prev_ts = self.prev_session_state.get(username, {}).get('timestamp', 'N/A')
                self.log(f"✅ [新消息] {username}")
                self.log(f"   当前 timestamp={timestamp}, 之前 timestamp={prev_ts}, 差值={timestamp - prev_ts if isinstance(prev_ts, int) else 'N/A'}秒")
                
                # 获取聊天显示名称
                display = self.contact_map.get(username, username)
                is_group = '@chatroom' in username
                
                # 调试日志：显示名称转换情况
                if display == username:
                    self.log(f"⚠️ 未找到联系人映射：{username} (使用原始 wxid)")
                else:
                    self.log(f"✓ 联系人映射：{username} -> {display}")
                
                # 获取发送者（群聊显示真实发送者，单聊显示聊天名称）
                if is_group:
                    final_sender = sender_name or self.contact_map.get(sender, sender)
                else:
                    final_sender = display
                
                # 解析消息内容
                if isinstance(summary, bytes):
                    try:
                        summary = summary.decode('utf-8', errors='replace')
                    except:
                        summary = '(压缩内容)'
                
                # 群消息格式：wxid_xxx:\n内容，提取内容
                if summary and ':\n' in summary:
                    summary = summary.split(':\n', 1)[1]
                
                # 根据消息类型显示
                msg_text = self.format_message_content(summary, msg_type)
                
                # 发送通知（立即推送，不等待）
                if final_sender and final_sender != display:
                    notification_text = f"{final_sender}: {msg_text}"
                else:
                    notification_text = msg_text
                
                # 获取联系人/群头像路径（命中缓存即时返回，未命中则同步下载）
                avatar_path = self.avatar_cache.get_avatar_path(username)
                
                self.log(f"📤 准备推送：{display} - {notification_text[:50]}")
                self.send_notification(display, notification_text, timestamp,
                                       icon_path=avatar_path, username=username)
                self.notified_contact_signal.emit(username, display)
                new_msgs.append(notification_text)
                
                # 更新状态
                self.prev_session_state[username] = {
                    'timestamp': timestamp,
                    'msg_type': msg_type,
                }
            
            self.log(f"📈 检查会话：{check_count}个，新消息：{new_count}个，跳过：{skip_count}个，"
                     f"手动过滤：{filter_manual_count}个，免打扰过滤：{filter_mute_count}个，"
                     f"公众号过滤：{filter_official_count}个")
            
            if new_msgs:
                self.log(f"🚀 推送 {len(new_msgs)} 条消息")
                # 发送消息数量信号
                self.msg_count_signal.emit(len(new_msgs))
                # 发送最后一条消息信号
                if new_msgs:
                    self.last_msg_signal.emit(new_msgs[-1][:20])
            else:
                self.log("ℹ️ 没有新消息")
        
        except sqlite3.DatabaseError as e:
            # 数据库损坏错误，通常是解密过程中微信正在写入
            # 忽略这次查询，等待下一次轮询
            if 'malformed' in str(e):
                self.log(f"数据库未就绪：{e}")
            else:
                self.log(f"数据库错误：{str(e)}")
        except Exception as e:
            self.log(f"处理消息失败：{str(e)}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
    
    def format_message_content(self, summary, msg_type):
        """根据消息类型格式化内容"""
        # 优先根据消息类型判断，即使 summary 为空也能显示类型
        if msg_type == 3:  # 图片
            return "[图片]"
        elif msg_type == 34:  # 语音
            return "[语音]"
        elif msg_type == 43:  # 视频
            return "[视频]"
        elif msg_type == 47:  # 表情/动画
            return "[表情]"
        elif msg_type == 49:  # 富媒体（链接、文件、小程序等）
            return "[富媒体消息]"
        elif msg_type == 50:  # 语音通话
            return "[语音通话]"
        elif msg_type == 10000:  # 系统消息
            return summary if summary else ""
        elif msg_type == 1:  # 文本
            if not summary:
                return ""
            return summary[:50] + "..." if len(summary) > 50 else summary
        else:
            # 其他类型，如果有 summary 就显示，否则显示类型
            if summary:
                return summary[:50] + "..." if len(summary) > 50 else summary
            else:
                return f"[类型{msg_type}]"
    
    def send_notification(self, sender, content, create_time, icon_path=None, username=None):
        """发送 Windows 通知

        Args:
            sender: 聊天显示名称（标题）
            content: 消息内容
            create_time: 消息时间戳
            icon_path: 联系人/群头像本地路径，为空则使用默认微信图标
            username: 联系人 username（用于调试日志）
        """
        if not self.config.get('enable_notify', True):
            return
            
        time_str = datetime.fromtimestamp(create_time).strftime('%H:%M:%S')
        title = f"{sender}"
        msg = f"{content[:50]}..." if len(content) > 50 else f"{content}"
        
        # 确定通知图标：优先使用联系人/群头像，失败则回退到默认微信图标
        final_icon = icon_path
        if not final_icon or not os.path.exists(final_icon):
            final_icon = self.default_icon
        
        # 使用 winotify 发送 Windows 10/11 通知
        if HAS_WINOTIFY:
            try:
                toast = Notification(
                    app_id="微信消息",
                    title=title,
                    msg=msg,
                    icon=final_icon
                )
                if self.enable_notification_sound:
                    try:
                        toast.set_audio(audio.Default, loop=False)
                    except Exception as audio_err:
                        self.log(f"winotify 设置声音失败，将使用系统声音兜底：{audio_err}")
                toast.show()
                self._play_notification_sound()
                icon_desc = "自定义头像" if (icon_path and os.path.exists(icon_path)) else "默认图标"
                self.log(f"winotify 通知已发送（{icon_desc}）：{title} - {msg}")
            except Exception as e:
                self.log(f"winotify 通知发送失败：{str(e)}")
                import traceback
                self.log(f"错误详情：{traceback.format_exc()}")
        else:
            self._play_notification_sound()
            self.log(f"通知：{title} - {msg}")

    def _play_notification_sound(self):
        """使用 winsound 兜底播放系统通知音，避免 Toast 静音。"""
        if not self.enable_notification_sound:
            return

        def _worker():
            try:
                # MessageBeep 使用系统通知方案；即使 Toast 声音被吞，也通常能触发。
                try:
                    ctypes.windll.user32.MessageBeep(0x00000040)  # MB_ICONASTERISK
                except Exception:
                    pass

                import winsound
                aliases = [
                    self.sound_alias or "SystemAsterisk",
                    "SystemNotification",
                    "SystemAsterisk",
                    "SystemExclamation",
                    "SystemDefault",
                ]
                seen = set()
                for alias in aliases:
                    if not alias or alias in seen:
                        continue
                    seen.add(alias)
                    try:
                        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
                        return
                    except Exception:
                        continue

                # 最后兜底：短促蜂鸣。某些机器没有蜂鸣设备时会失败，忽略即可。
                try:
                    winsound.Beep(880, 120)
                except Exception:
                    pass
            except Exception as e:
                self.log(f"系统通知声音播放失败：{e}")

        threading.Thread(target=_worker, daemon=True, name="notify-sound").start()

    def run(self):
        self.running = True
        self.status_signal.emit("running")
        
        self.log("=" * 60)
        self.log("🎯 微信消息监听服务启动")
        self.log("=" * 60)
        
        if not self.init_wx_env():
            self.status_signal.emit("error")
            return

        self.log("📚 加载联系人列表...")
        self.load_contacts()
        
        self.log("=" * 60)
        self.log("✅ 初始同步完成")
        self.log(f"📊 已记录 {len(self.prev_session_state)} 个会话状态")
        self.log("=" * 60)
        
        # 使用主动轮询模式（参考 wechat-decrypt，30ms 延迟）
        self.log("🔄 启动轮询监听（30ms 间隔）...")
        self.log("💡 提示：新消息会在 30ms 内检测并推送到 Windows 通知")
        self.log("=" * 60)
        self.run_polling()
        
        self.status_signal.emit("stopped")
        self.log("🛑 监听服务已停止")
        self.finished.emit()

    def stop(self):
        """停止工作线程"""
        self.running = False
        
        # 关闭数据库连接
        try:
            if hasattr(self, 'contact_conn') and self.contact_conn:
                self.contact_conn.close()
        except:
            pass
        
        # 关闭 session 数据库连接（如果有）
        try:
            if hasattr(self, 'session_conn') and self.session_conn:
                self.session_conn.close()
        except:
            pass
        
        self.log("工作线程停止信号已发送")

# --- UI 界面部分 ---

class Interface(QWidget):
    def __init__(self, parent=None, config=None):
        super().__init__(parent=parent)
        self.setObjectName('Interface')
        self.worker = None
        self.thread = None
        self.config = config or {}
        self.available_contacts = {}
        self.notified_contacts = {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        self.setting_group = SettingCardGroup("运行配置", self)
        
        # 从配置中读取默认值
        gui_config = self.config.get('gui', {})
        self.dir_temp_path = self.config.get('temp_dir', os.path.join(os.getcwd(), "wx_temp_data"))
        if not os.path.isabs(self.dir_temp_path):
            self.dir_temp_path = os.path.join(os.getcwd(), self.dir_temp_path)
        
        debounce_default = gui_config.get('debounce_time_ms', 1000)
        notify_duration_default = gui_config.get('notify_duration_sec', 5)
        filter_mute_default = gui_config.get('filter_mute', True)
        filter_official_default = gui_config.get('filter_official_article', True)
        self.mute_usernames = gui_config.get('mute_usernames', [])
        if not isinstance(self.mute_usernames, list):
            self.mute_usernames = []
        self.enable_notification_sound = gui_config.get('enable_notification_sound', True)
        self.sound_alias = gui_config.get('sound_alias', 'SystemAsterisk')
        
        # 1. 启动/停止
        self.switch_card = SwitchSettingCard(
            FIF.PLAY, 
            "服务状态", 
            "开启后自动监听微信新消息并推送通知"
        )
        self.switch_card.checkedChanged.connect(self.toggle_service)
        self.setting_group.addSettingCard(self.switch_card)
        
        # 2. 临时目录
        self.dir_card = PushSettingCard(
            "选择文件夹",
            FIF.FOLDER,
            "数据缓存目录",
            "用于存放解密后的临时数据库文件"
        )
        self.dir_card.setContent(self.dir_temp_path)
        self.dir_card.clicked.connect(self.choose_dir)
        self.setting_group.addSettingCard(self.dir_card)
        
        # 3. 防抖动时间 (使用自定义 SliderSettingCard)
        self.debounce_card = SliderSettingCard(
            1000, 5000, 100, debounce_default, 
            FIF.HISTORY, 
            "消息防抖动 (ms)", 
            "避免微信连续写入导致重复解密 (推荐 1000ms)"
        )
        self.setting_group.addSettingCard(self.debounce_card)
        
        # 4. 通知停留时间 (使用自定义 SliderSettingCard)
        self.duration_card = SliderSettingCard(
            1, 30, 1, notify_duration_default,
            FIF.INFO,
            "通知停留时间 (秒)",
            "Windows 通知显示的持续时间"
        )
        self.setting_group.addSettingCard(self.duration_card)
        
        # 5. 过滤免打扰消息
        # 图标做回退保护，避免某些 qfluentwidgets 版本缺少对应枚举导致启动崩溃
        try:
            mute_icon = FIF.MUTE
        except AttributeError:
            mute_icon = FIF.CANCEL
        self.filter_mute_card = SwitchSettingCard(
            mute_icon,
            "过滤消息免打扰",
            "已设置消息免打扰的联系人/群不再弹窗提醒"
        )
        self.filter_mute_card.setChecked(filter_mute_default)
        self.setting_group.addSettingCard(self.filter_mute_card)
        
        # 6. 过滤公众号文章推送
        try:
            official_icon = FIF.MESSAGE
        except AttributeError:
            official_icon = FIF.INFO
        self.filter_official_card = SwitchSettingCard(
            official_icon,
            "过滤公众号文章推送",
            "公众号发布的文章/消息不再弹窗提醒"
        )
        self.filter_official_card.setChecked(filter_official_default)
        self.setting_group.addSettingCard(self.filter_official_card)

        # 7. 通知声音
        try:
            sound_icon = FIF.VOLUME
        except AttributeError:
            sound_icon = FIF.INFO
        self.sound_card = SwitchSettingCard(
            sound_icon,
            "通知声音",
            "开启后使用 Toast 声音 + Windows 系统声音兜底"
        )
        self.sound_card.setChecked(self.enable_notification_sound)
        self.sound_card.checkedChanged.connect(self.on_sound_toggled)
        self.setting_group.addSettingCard(self.sound_card)
        
        layout.addWidget(self.setting_group)

        # 手动过滤：使用独立面板，不放进 SettingCard，避免列表控件被固定行高压扁。
        manual_title = SubtitleLabel("手动过滤", self)
        layout.addWidget(manual_title)

        manual_panel = QWidget(self)
        manual_panel.setMinimumHeight(360)
        manual_panel.setObjectName("ManualFilterPanel")
        manual_panel.setStyleSheet(
            "QWidget#ManualFilterPanel { background: rgba(255,255,255,0.96); "
            "border: 1px solid #DDE3EA; border-radius: 12px; }"
            "QLineEdit { padding: 8px 10px; border: 1px solid #D0D7DE; "
            "border-radius: 8px; background: #FFFFFF; }"
            "QListWidget { border: 1px solid #D0D7DE; border-radius: 8px; "
            "background: #FFFFFF; padding: 4px; }"
            "QPushButton { padding: 8px 12px; border-radius: 8px; "
            "background: #E8F3FF; border: 1px solid #B8D8FF; }"
            "QPushButton:hover { background: #D8EAFF; }"
        )
        manual_layout = QVBoxLayout(manual_panel)
        manual_layout.setContentsMargins(18, 16, 18, 16)
        manual_layout.setSpacing(10)

        manual_header = BodyLabel("手动过滤联系人/群：命中后不再弹窗。可从联系人或已弹窗提醒记录中搜索添加。", self)
        manual_header.setTextColor(QColor(60, 60, 60), QColor(220, 220, 220))
        manual_layout.addWidget(manual_header)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.mute_search_edit = QLineEdit(self)
        self.mute_search_edit.setPlaceholderText("搜索昵称、群名、username；也可直接粘贴 wxid_xxx 或 xxx@chatroom")
        self.mute_search_edit.textChanged.connect(self.refresh_contact_candidates)
        self.add_mute_btn = QPushButton("添加到过滤", self)
        self.add_mute_btn.clicked.connect(self.add_selected_mute_contact)
        search_row.addWidget(self.mute_search_edit, 1)
        search_row.addWidget(self.add_mute_btn)
        manual_layout.addLayout(search_row)

        lists_row = QHBoxLayout()
        lists_row.setSpacing(12)

        candidate_box = QVBoxLayout()
        candidate_box.setSpacing(6)
        candidate_label = BodyLabel("可添加候选（已加载联系人 / 已弹窗提醒）", self)
        candidate_box.addWidget(candidate_label)
        self.contact_candidate_list = QListWidget(self)
        self.contact_candidate_list.setMinimumHeight(190)
        self.contact_candidate_list.itemDoubleClicked.connect(lambda _item: self.add_selected_mute_contact())
        candidate_box.addWidget(self.contact_candidate_list)
        lists_row.addLayout(candidate_box, 1)

        current_box = QVBoxLayout()
        current_box.setSpacing(6)
        current_label = BodyLabel("当前过滤列表", self)
        current_box.addWidget(current_label)

        current_row = QHBoxLayout()
        current_row.setSpacing(8)
        self.manual_mute_list = QListWidget(self)
        self.manual_mute_list.setMinimumHeight(190)
        current_buttons = QVBoxLayout()
        current_buttons.setSpacing(6)
        self.remove_mute_btn = QPushButton("移除选中", self)
        self.remove_mute_btn.clicked.connect(self.remove_selected_mute_contact)
        self.clear_mute_btn = QPushButton("清空列表", self)
        self.clear_mute_btn.clicked.connect(self.clear_manual_mute_contacts)
        self.save_mute_btn = QPushButton("保存配置", self)
        self.save_mute_btn.clicked.connect(lambda: self.save_manual_mute_settings(show_tip=True))
        current_buttons.addWidget(self.remove_mute_btn)
        current_buttons.addWidget(self.clear_mute_btn)
        current_buttons.addWidget(self.save_mute_btn)
        current_buttons.addStretch()
        current_row.addWidget(self.manual_mute_list, 1)
        current_row.addLayout(current_buttons)
        current_box.addLayout(current_row)
        lists_row.addLayout(current_box, 1)

        manual_layout.addLayout(lists_row)

        manual_tip = BodyLabel("提示：优先从候选里添加，这样保存的是真实 username；直接输入群名/昵称也会按显示名匹配。", self)
        manual_tip.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))
        manual_layout.addWidget(manual_tip)

        layout.addWidget(manual_panel)
        self.refresh_mute_list()
        self.refresh_contact_candidates()
        
        # 状态面板
        self.status_group = SettingCardGroup("实时监控", self)
        
        # 创建状态卡片
        status_card = SettingCard(FIF.INFO, "监控状态", "实时显示服务运行情况", self.status_group)
        
        # 状态信息
        self.status_label = BodyLabel("当前状态：未运行")
        self.status_label.setTextColor(QColor(100, 100, 100), QColor(200, 200, 200))  # 浅色主题，深色主题
        status_card.layout().addWidget(self.status_label)
        status_card.layout().setContentsMargins(16, 16, 16, 16)
        
        self.status_group.addSettingCard(status_card)
        
        layout.addWidget(self.status_group)
        
        # 日志区域 - 底部可调整大小的文本框
        # 添加日志标题
        log_title = SubtitleLabel("运行日志", self)
        layout.addWidget(log_title)
        
        # 创建可调整大小的文本框
        self.log_text = TextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        self.log_text.setMaximumHeight(500)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet(
            "TextEdit { border-radius: 10px; padding: 8px; "
            "background: rgba(248, 249, 250, 0.92); }"
        )
        
        # 设置布局权重，让日志区域可以随窗口大小调整
        layout.addWidget(self.log_text, 1)
        
        layout.addStretch()
        self.setLayout(layout)

    def choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择缓存目录", self.dir_temp_path)
        if path:
            self.dir_temp_path = path
            self.dir_card.setContent(path)

    def _contact_display(self, username):
        return (
            self.notified_contacts.get(username)
            or self.available_contacts.get(username)
            or username
        )

    def refresh_contact_candidates(self):
        """刷新手动过滤候选列表。"""
        if not hasattr(self, 'contact_candidate_list'):
            return

        query = self.mute_search_edit.text().strip().lower() if hasattr(self, 'mute_search_edit') else ""
        self.contact_candidate_list.clear()

        merged = {}
        sources = {}
        for username, name in self.available_contacts.items():
            merged[username] = name or username
            sources[username] = "联系人"
        for username, name in self.notified_contacts.items():
            merged[username] = name or username
            sources[username] = "已提醒"

        def _match(item):
            username, name = item
            haystack = f"{username} {name}".lower()
            return not query or query in haystack

        rows = sorted(
            [item for item in merged.items() if _match(item)],
            key=lambda kv: (0 if sources.get(kv[0]) == "已提醒" else 1, kv[1], kv[0])
        )

        for username, name in rows[:80]:
            if username in self.mute_usernames:
                continue
            item = QListWidgetItem(f"[{sources.get(username, '联系人')}] {name}    <{username}>")
            item.setData(Qt.UserRole, username)
            self.contact_candidate_list.addItem(item)

        # 没有匹配项时允许用户直接添加输入的 username。
        if query and self.contact_candidate_list.count() == 0:
            raw = self.mute_search_edit.text().strip()
            item = QListWidgetItem(f"[手动输入] {raw}")
            item.setData(Qt.UserRole, raw)
            self.contact_candidate_list.addItem(item)

    def refresh_mute_list(self):
        """刷新当前手动过滤列表。"""
        if not hasattr(self, 'manual_mute_list'):
            return

        self.manual_mute_list.clear()
        for username in sorted(set(self.mute_usernames)):
            name = self._contact_display(username)
            item = QListWidgetItem(f"{name}    <{username}>")
            item.setData(Qt.UserRole, username)
            self.manual_mute_list.addItem(item)

    def add_selected_mute_contact(self):
        """从候选列表或搜索框添加一个手动过滤对象。"""
        username = ""
        item = self.contact_candidate_list.currentItem() if hasattr(self, 'contact_candidate_list') else None
        if item:
            username = item.data(Qt.UserRole) or ""
        if not username and hasattr(self, 'mute_search_edit'):
            username = self.mute_search_edit.text().strip()

        username = (username or "").strip()
        if not username:
            return

        current = set(self.mute_usernames)
        current.add(username)
        self.mute_usernames = sorted(current)
        self.refresh_mute_list()
        self.refresh_contact_candidates()
        self.save_manual_mute_settings(show_tip=False)

    def remove_selected_mute_contact(self):
        item = self.manual_mute_list.currentItem() if hasattr(self, 'manual_mute_list') else None
        if not item:
            return

        username = item.data(Qt.UserRole)
        self.mute_usernames = [u for u in self.mute_usernames if u != username]
        self.refresh_mute_list()
        self.refresh_contact_candidates()
        self.save_manual_mute_settings(show_tip=False)

    def clear_manual_mute_contacts(self):
        self.mute_usernames = []
        self.refresh_mute_list()
        self.refresh_contact_candidates()
        self.save_manual_mute_settings(show_tip=False)

    def save_manual_mute_settings(self, show_tip=False):
        """持久化手动过滤列表，并同步到正在运行的 worker。"""
        try:
            from utils.gui_config import save_config
            if 'gui' not in self.config:
                self.config['gui'] = {}
            self.config['gui']['mute_usernames'] = list(self.mute_usernames)
            self.config['gui']['enable_notification_sound'] = (
                self.sound_card.isChecked() if hasattr(self, 'sound_card') else self.enable_notification_sound
            )
            self.config['gui']['sound_alias'] = self.sound_alias
            save_config(self.config)

            if self.worker:
                self.worker.manual_mute_set = set(self.mute_usernames)
                self.worker.enable_notification_sound = self.config['gui']['enable_notification_sound']

            if show_tip:
                InfoBar.success(
                    "配置已保存",
                    f"当前手动过滤 {len(self.mute_usernames)} 个联系人/群",
                    position=InfoBarPosition.TOP,
                    parent=self
                )
        except Exception as e:
            logging.warning(f"保存手动过滤配置失败：{e}")
            if show_tip:
                InfoBar.error("保存失败", str(e), position=InfoBarPosition.TOP, parent=self)

    def on_sound_toggled(self, checked):
        self.enable_notification_sound = checked
        if self.worker:
            self.worker.enable_notification_sound = checked
        self.save_manual_mute_settings(show_tip=False)

    def update_contact_candidates(self, contact_map):
        """接收 worker 已加载的联系人映射。"""
        if isinstance(contact_map, dict):
            self.available_contacts.update(contact_map)
            self.refresh_mute_list()
            self.refresh_contact_candidates()

    def add_notified_contact_candidate(self, username, display_name):
        """记录已经弹窗提醒过的联系人/群，供用户快速加入过滤。"""
        if username:
            self.notified_contacts[username] = display_name or username
            self.refresh_mute_list()
            self.refresh_contact_candidates()

    def toggle_service(self, checked):
        if checked:
            self.start_service()
        else:
            self.stop_service()

    def start_service(self):
        import logging
        logging.info("开始启动服务...")
        
        # 先初始化微信环境（GUI 模式）
        from core.wx_decrypt import init_wechat_env
        logging.info("初始化微信环境...")
        if not init_wechat_env():
            logging.error("微信环境初始化失败")
            InfoBar.error("启动失败", "无法初始化微信环境，请检查配置", position=InfoBarPosition.TOP, parent=self)
            return
        
        config = {
            'temp_dir': self.dir_temp_path,
            'keys_file': self.config.get('keys_file'),
            'debounce_time': self.debounce_card.value / 1000.0,
            'notify_duration': self.duration_card.value,
            'enable_notify': True,
            'filter_mute': self.filter_mute_card.isChecked(),
            'filter_official_article': self.filter_official_card.isChecked(),
            'mute_usernames': self.mute_usernames,
            'enable_notification_sound': self.sound_card.isChecked(),
            'sound_alias': self.sound_alias,
        }

        # 持久化过滤设置到配置文件，便于下次启动时记忆
        try:
            from utils.gui_config import save_config
            if 'gui' not in self.config:
                self.config['gui'] = {}
            self.config['gui']['filter_mute'] = config['filter_mute']
            self.config['gui']['filter_official_article'] = config['filter_official_article']
            self.config['gui']['mute_usernames'] = config['mute_usernames']
            self.config['gui']['enable_notification_sound'] = config['enable_notification_sound']
            self.config['gui']['sound_alias'] = config['sound_alias']
            save_config(self.config)
        except Exception as e:
            logging.warning(f"保存过滤设置失败：{e}")

        self.worker = WeChatMonitorWorker(config)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        
        self.worker.log_signal.connect(self.update_log)
        self.worker.status_signal.connect(self.update_status)
        self.worker.contact_map_signal.connect(self.update_contact_candidates)
        self.worker.notified_contact_signal.connect(self.add_notified_contact_candidate)
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        logging.info("启动工作线程...")
        self.thread.start()
        logging.info("工作线程已启动")
        InfoBar.success("服务已启动", "开始监听微信消息...", position=InfoBarPosition.TOP, parent=self)
        logging.info("服务启动完成")
        
        # 更新托盘菜单
        if hasattr(self, 'tray_menu'):
            self.update_service_menu()

    def stop_service(self):
        import logging
        logging.info("开始停止服务...")
        
        if self.worker:
            logging.info("停止工作线程...")
            
            # 先设置停止标志
            self.worker.stop()
            
            # 等待线程退出（不阻塞 GUI）
            if self.thread and self.thread.isRunning():
                logging.info("等待线程退出...")
                
                # 使用 quit() 请求线程退出
                self.thread.quit()
                
                # 保存线程引用，避免在 QTimer 回调中访问已删除的对象
                thread_ref = self.thread
                
                # 使用 QTimer 异步等待，避免阻塞 GUI
                from PyQt5.QtCore import QTimer
                def check_thread():
                    try:
                        if thread_ref and not thread_ref.isRunning():
                            logging.info("线程已退出")
                            self._cleanup_service()
                        elif thread_ref:
                            # 如果 3 秒后还在运行，强制终止
                            QTimer.singleShot(100, check_thread)
                        else:
                            # 线程引用已清除，直接清理
                            self._cleanup_service()
                    except RuntimeError:
                        # 线程对象已被删除
                        logging.info("线程对象已销毁")
                        self._cleanup_service()
                
                QTimer.singleShot(100, check_thread)
                return  # 提前返回，让 QTimer 处理后续清理
            
            self._cleanup_service()
        else:
            self._cleanup_service()
    
    def _cleanup_service(self):
        """清理服务资源"""
        import logging
        
        if self.worker:
            self.worker = None
        if self.thread:
            self.thread = None
        
        logging.info("服务已停止")
        InfoBar.warning("服务已停止", "监听已关闭", position=InfoBarPosition.TOP, parent=self)
        self.update_status("stopped")
        
        # 更新托盘菜单
        if hasattr(self, 'tray_menu'):
            self.update_service_menu()

    def update_log(self, msg):
        self.log_text.append(msg)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_status(self, status):
        if status == "running":
            self.status_label.setText("当前状态：🟢 运行中")
            self.status_label.setStyleSheet("font-weight: bold; color: #107C10;")
            self.switch_card.setChecked(True)
        elif status == "error":
            self.status_label.setText("当前状态：🔴 错误")
            self.status_label.setStyleSheet("font-weight: bold; color: #D13438;")
            self.switch_card.setChecked(False)
            InfoBar.error("运行错误", "请检查管理员权限或微信登录状态", position=InfoBarPosition.TOP, parent=self)
        else:
            self.status_label.setText("当前状态：⚪ 已停止")
            self.status_label.setStyleSheet("font-weight: bold; color: #888;")
            self.switch_card.setChecked(False)

class MainWindow(FluentWidget):
    def __init__(self):
        # 必须先初始化父类，Mica 效果会在父类初始化中自动应用
        super().__init__()

        # 自动跟随系统主题（深色/浅色）
        from qfluentwidgets import Theme, setTheme
        setTheme(Theme.AUTO)
        
        # 设置窗口图标和标题
        icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("微信消息通知助手")
        
        # 设置窗口大小为全屏最大化
        self.setWindowState(Qt.WindowMaximized)
        
        # 设置一个较大的默认尺寸
        self.resize(1200, 800)

        # 顶层布局改为垂直布局，结构更清晰
        self.main_layout = QVBoxLayout(self)
        # 留出标题栏的空间
        self.main_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.main_layout.setSpacing(10)

        # 初始化配置（首次运行时自动创建配置文件）
        self.config = None
        self.config_is_new = False
        self._init_config()
        
        # 添加切换用户按钮到状态栏
        self._add_user_switch_button()

        # 创建并添加接口
        self.interface = Interface(self, self.config)
        self.main_layout.addWidget(self.interface)
        
        if not ctypes.windll.shell32.IsUserAnAdmin():
            InfoBar.warning(
                title="权限提示",
                content="建议以管理员身份运行以获取微信密钥",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self
            )
        
        # 初始化系统托盘
        self._init_system_tray(icon_path)
    
    def on_theme_changed(self):
        """主题变化时的回调函数"""
        pass
    
    def _init_config(self):
        """初始化配置文件"""
        try:
            # 扫描所有微信用户
            from utils.gui_config import scan_all_wechat_dirs
            candidates = scan_all_wechat_dirs()
            
            # 检测是否有多个用户
            if len(candidates) > 1:
                logger.info(f"检测到 {len(candidates)} 个微信账号，等待用户选择")
                # 延迟显示用户选择器，等待 GUI 完全初始化
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(500, lambda: self._show_user_selector(candidates))
                return
            
            # 单个用户或无用户，继续正常初始化
            self._load_config_from_candidates(candidates[0] if candidates else None)
            
        except Exception as e:
            logger.error(f"配置初始化失败：{e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # 使用默认配置
            self.config = {
                'temp_dir': os.path.join(os.getcwd(), "wx_temp_data"),
                'debounce_time': 1.0,
                'notify_duration': 5,
                'enable_notify': True
            }
            InfoBar.error(
                title="配置加载失败",
                content="使用默认配置，部分功能可能受限",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=5000
            )
    
    def _show_user_selector(self, candidates: list):
        """显示用户选择器"""
        from ui.user_selector import show_user_selector
        
        def on_selected(user_data):
            """用户选择完成"""
            logger.info(f"用户选择完成：{user_data['wxid']}")
            self._load_config_from_candidates(user_data)
            InfoBar.success(
                title="用户切换成功",
                content=f"已切换到账号：{user_data['wxid']}",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=3000
            )
        
        def on_cancelled():
            """用户取消选择"""
            logger.warning("用户取消选择，使用默认配置")
            # 使用默认配置（第一个/最新的用户）
            self._load_config_from_candidates(candidates[0])
            InfoBar.warning(
                title="已取消选择",
                content="将使用默认配置（最新账号）",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=3000
            )
        
        # 显示用户选择器（相对于窗口中央显示）
        # 使用窗口中心位置作为参考点
        show_user_selector(
            candidates,
            self,  # 使用窗口本身作为参考
            self,
            on_selected=on_selected,
            on_cancelled=on_cancelled
        )
    
    def _load_config_from_candidates(self, user_data: dict = None):
        """从候选用户数据加载配置"""
        try:
            from utils.gui_config import ensure_config_file, validate_keys_file, save_config
            from core.wx_decrypt import set_gui_config
            
            # 确保配置文件存在
            self.config, self.config_is_new = ensure_config_file()
            
            # 如果有用户数据，更新配置
            if user_data:
                self.config['db_dir'] = user_data['path']
                self.config['wxid'] = user_data['wxid']
                # 保存更新后的配置
                save_config(self.config)
                logger.info(f"配置已更新为：{user_data['path']}")
            
            # 设置 wx_decrypt 模块的 GUI 配置
            set_gui_config(
                self.config.get('db_dir', ''),
                self.config.get('wxid', ''),
                self.config.get('keys_file')
            )
            
            # 验证密钥文件
            keys_file = self.config.get('keys_file', 'all_keys.json')
            if not os.path.isabs(keys_file):
                keys_file = os.path.join(os.path.dirname(__file__), keys_file)
            
            keys_valid = validate_keys_file(keys_file)
            
            # 显示配置状态提示
            if self.config_is_new:
                logger.info("已创建新的配置文件")
                InfoBar.success(
                    title="配置初始化",
                    content=f"已自动创建配置文件，{'检测到微信数据目录' if self.config.get('db_dir') else '请手动配置 db_dir'}",
                    position=InfoBarPosition.TOP,
                    parent=self,
                    duration=5000
                )
            else:
                logger.info("配置文件已加载")
            
            # 如果密钥文件不存在或无效，显示提示
            if not keys_valid:
                InfoBar.warning(
                    title="密钥文件缺失",
                    content="软件将自动检测并提取密钥，请确保微信已登录",
                    position=InfoBarPosition.TOP,
                    parent=self,
                    duration=5000
                )
                logger.warning("密钥文件无效，将尝试自动提取")
                
                # 自动提取密钥
                try:
                    from utils.auto_extract_keys import extract_keys
                    logger.info("开始自动提取密钥...")
                    
                    # 在线程中执行提取，避免阻塞 UI
                    import threading
                    def extract_thread():
                        try:
                            success = extract_keys()
                            if success:
                                logger.info("密钥提取成功")
                                # 重新加载配置
                                self._init_config()
                            else:
                                logger.error("密钥提取失败")
                        except Exception as e:
                            logger.error(f"密钥提取异常：{e}")
                    
                    thread = threading.Thread(target=extract_thread, daemon=True)
                    thread.start()
                    
                except Exception as e:
                    logger.error(f"无法启动密钥提取：{e}")
            
            # 记录配置信息
            logger.info(f"数据库路径：{self.config.get('db_dir', '未设置')}")
            logger.info(f"密钥文件：{keys_file}")
            logger.info(f"密钥状态：{'有效' if keys_valid else '无效/缺失'}")
            
        except Exception as e:
            logger.error(f"配置加载失败：{e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # 使用默认配置
            self.config = {
                'temp_dir': os.path.join(os.getcwd(), "wx_temp_data"),
                'debounce_time': 1.0,
                'notify_duration': 5,
                'enable_notify': True
            }
            InfoBar.error(
                title="配置加载失败",
                content="使用默认配置，部分功能可能受限",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=5000
            )
    
    def _add_user_switch_button(self):
        """添加切换用户按钮到状态栏"""
        from qfluentwidgets import Action, RoundMenu
        from PyQt5.QtWidgets import QPushButton
        
        # 创建切换用户按钮
        self.user_switch_btn = QPushButton('切换用户')
        self.user_switch_btn.setFixedWidth(100)
        self.user_switch_btn.clicked.connect(self._on_user_switch_clicked)
        
        # 添加到状态面板
        if hasattr(self, 'interface') and hasattr(self.interface, 'status_group'):
            status_layout = self.interface.status_group.layout()
            if status_layout:
                status_layout.addWidget(self.user_switch_btn)
    
    def _on_user_switch_clicked(self):
        """切换用户按钮点击事件"""
        from utils.gui_config import scan_all_wechat_dirs
        from ui.user_selector import show_user_selector
        
        candidates = scan_all_wechat_dirs()
        
        if not candidates:
            InfoBar.warning(
                title="未检测到微信账号",
                content="请先启动微信并登录",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=3000
            )
            return
        
        def on_selected(user_data):
            """用户选择完成"""
            logger.info(f"用户切换：{user_data['wxid']}")
            # 更新配置
            self.config['db_dir'] = user_data['path']
            from utils.gui_config import save_config
            save_config(self.config)
            
            # 刷新界面显示
            InfoBar.success(
                title="切换成功",
                content=f"已切换到：{user_data['wxid']}",
                position=InfoBarPosition.TOP,
                parent=self,
                duration=3000
            )
            
            # 如果服务正在运行，提示重启
            if self.interface.worker and self.interface.worker.running:
                InfoBar.warning(
                    title="需要重启服务",
                    content="请先停止服务，然后重新启动以应用新配置",
                    position=InfoBarPosition.TOP,
                    parent=self,
                    duration=5000
                )
        
        def on_cancelled():
            """用户取消选择"""
            logger.debug("用户取消切换")
        
        # 显示用户选择器
        show_user_selector(
            candidates,
            self.user_switch_btn,
            self,
            on_selected=on_selected,
            on_cancelled=on_cancelled
        )
    
    def _init_system_tray(self, icon_path):
        """初始化系统托盘图标和菜单"""
        from qfluentwidgets import RoundMenu, Action, FluentIcon
        
        # 创建系统托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(icon_path))
        self.tray_icon.setToolTip("微信消息通知助手")
        
        # 保存图标路径供后续使用
        self.tray_icon_path = icon_path
        
        # 创建托盘菜单（使用 RoundMenu）
        # RoundMenu 默认会在点击选项或失去焦点时自动关闭
        self.tray_menu = RoundMenu(parent=self)
        
        # 显示主窗口动作
        self.tray_menu.addAction(Action(FluentIcon.VIEW, '显示主窗口', triggered=self.show_window))
        
        # 添加分割线
        self.tray_menu.addSeparator()
        
        # 启动/停止服务动作（动态更新）
        self.update_service_menu()
        
        # 添加分割线
        self.tray_menu.addSeparator()
        
        # 退出动作
        self.tray_menu.addAction(Action(FluentIcon.CLOSE, '退出', triggered=self.quit_app))
        
        # 设置托盘菜单
        self.tray_icon.setContextMenu(self.tray_menu)
        
        # 连接双击信号
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
        # 显示托盘图标
        self.tray_icon.show()
    
    def update_service_menu(self):
        """根据服务状态更新菜单"""
        from qfluentwidgets import Action, FluentIcon
        
        # 清除旧的服务相关动作
        for action in self.tray_menu.actions():
            if action.text() in ['启动服务', '停止服务']:
                self.tray_menu.removeAction(action)
        
        # 根据服务状态添加对应的动作
        is_running = self.interface.worker and self.interface.worker.running
        
        if is_running:
            # 服务运行中，显示"停止服务"
            stop_action = Action(FluentIcon.CANCEL, '停止服务', triggered=self.stop_service_from_menu)
            # 在服务动作前插入（在分割线之后）
            actions = self.tray_menu.actions()
            for i, action in enumerate(actions):
                if action.isSeparator() and i > 0:
                    self.tray_menu.insertAction(actions[i], stop_action)
                    break
        else:
            # 服务未运行，显示"启动服务"
            start_action = Action(FluentIcon.PLAY, '启动服务', triggered=self.start_service_from_menu)
            # 在服务动作前插入（在分割线之后）
            actions = self.tray_menu.actions()
            for i, action in enumerate(actions):
                if action.isSeparator() and i > 0:
                    self.tray_menu.insertAction(actions[i], start_action)
                    break
    
    def start_service_from_menu(self):
        """从菜单启动服务"""
        self.interface.start_service()
        # 更新菜单
        self.update_service_menu()
    
    def stop_service_from_menu(self):
        """从菜单停止服务"""
        self.interface.stop_service()
        # 更新菜单
        self.update_service_menu()
    
    def on_tray_icon_activated(self, reason):
        """托盘图标被激活时的处理"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()
    
    def show_window(self):
        """显示主窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()
    
    def quit_app(self):
        """退出应用"""
        try:
            if self.interface.worker:
                self.interface.stop_service()
            self.tray_icon.hide()
            QApplication.quit()
        except RuntimeError:
            # 对象已被删除
            logging.info("界面对象已销毁，直接退出")
            QApplication.quit()
    
    def closeEvent(self, event):
        """处理窗口关闭事件 - 最小化到托盘而不是退出"""
        try:
            # 如果有后台服务在运行，则最小化到托盘
            if self.interface.worker and self.interface.worker.running:
                event.ignore()  # 忽略关闭事件
                self.hide()  # 隐藏窗口
                self.tray_icon.showMessage(
                    "微信消息通知助手",
                    "已最小化到系统托盘，双击托盘图标可恢复窗口",
                    QSystemTrayIcon.Information,
                    2000
                )
            else:
                # 如果服务未运行，直接退出
                event.accept()
                self.quit_app()
        except RuntimeError:
            # 对象已被删除，直接接受关闭
            event.accept()
            QApplication.quit()

if __name__ == "__main__":
    # 配置日志记录 - 使用程序所在目录
    if getattr(sys, 'frozen', False):
        # 如果是编译后的 exe，使用 exe 所在目录
        log_dir = os.path.dirname(sys.executable)
    else:
        # 如果是源码运行，使用源码所在目录
        log_dir = os.path.dirname(__file__)
    
    log_file = os.path.join(log_dir, 'wx_gui_notifier.log')
    
    # 确保日志目录存在
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    logging.info("=" * 60)
    logging.info("微信消息通知助手启动")
    logging.info(f"日志文件：{log_file}")
    logging.info(f"程序目录：{log_dir}")
    logging.info(f"是否冻结：{getattr(sys, 'frozen', False)}")
    logging.info("=" * 60)
    
    # 添加全局异常处理器
    def exception_handler(exc_type, exc_value, exc_tb):
        logging.critical("未捕获的异常！", exc_info=(exc_type, exc_value, exc_tb))
        logging.critical(f"异常类型：{exc_type}")
        logging.critical(f"异常值：{exc_value}")
        import traceback
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.critical(f"堆栈跟踪:\n{tb_str}")
    
    sys.excepthook = exception_handler
    
    # PyQt5 的异常处理
    class LogHandler(QObject):
        @pyqtSlot(str)
        def message_handler(self, msg):
            logging.error(f"Qt 消息：{msg}")
    
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

        app = QApplication(sys.argv)
        
        # 设置应用图标
        icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.ico')
        app.setWindowIcon(QIcon(icon_path))
        
        from qfluentwidgets import Theme, setTheme
        setTheme(Theme.AUTO)
        
        logging.info("初始化主窗口...")
        w = MainWindow()
        logging.info("主窗口初始化完成")
        w.show()  # 正常显示主窗口
        logging.info("主窗口已显示")
        
        logging.info("启动应用事件循环...")
        exit_code = sys.exit(app.exec_())
        logging.info(f"应用退出，退出码：{exit_code}")
        
    except Exception as e:
        logging.critical(f"启动过程中发生异常：{e}", exc_info=True)
        import traceback
        tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
        logging.critical(f"堆栈跟踪:\n{tb_str}")
        raise
