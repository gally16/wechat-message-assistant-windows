import sys
import os
import time
import sqlite3
import logging
import threading
import ctypes
import struct
from datetime import datetime
from collections import deque

# 确保 pkg_resources 可用
try:
    import pkg_resources
except ImportError:
    # 尝试从 setuptools 中导入
    try:
        from setuptools import pkg_resources
    except ImportError:
        pass

# PyQt5 & QFluentWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QSize, pyqtSlot
from PyQt5.QtGui import QIcon, QFont, QColor
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QSlider, QSpinBox, QLabel, QSystemTrayIcon, QMenu, QAction

from qfluentwidgets import (
    FluentWidget, NavigationItemPosition, MessageBox, 
    SettingCardGroup, PushSettingCard, SwitchSettingCard, 
    InfoBar, InfoBarPosition, 
    ComboBoxSettingCard, TitleLabel, SubtitleLabel,
    BodyLabel, TextEdit, SettingCard, ConfigItem, qconfig,
    SpinBox, ProgressBar, SystemThemeListener
)
# 尝试导入 SpinBoxSettingCard，如果失败则使用自定义
try:
    from qfluentwidgets import SpinBoxSettingCard
    HAS_SPINBOX_CARD = True
except ImportError:
    HAS_SPINBOX_CARD = False

from qfluentwidgets.common.icon import FluentIcon as FIF

# 业务逻辑依赖
try:
    # 使用 wechat-decrypt 替代 pywxdump，支持微信 4.x
    from wx_decrypt import get_wx_info, decrypt, HAS_DECRYPT
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    # 使用 winotify 实现 Windows 通知
    try:
        from winotify import Notification, audio
        HAS_WINOTIFY = True
    except ImportError:
        HAS_WINOTIFY = False
        
    if not HAS_DECRYPT:
        print("警告：wechat-decrypt 模块不可用，请先运行密钥提取")
except ImportError as e:
    print(f"缺少必要的库：{e}")
    print("请运行：pip install watchdog xmltodict zstandard pycryptodome cryptography pillow yara-python aiofiles")
    sys.exit(1)

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
            
            # 计算 session.db 路径
            wx_dir = os.path.dirname(os.path.dirname(self.db_path))
            self.session_db_path = os.path.join(wx_dir, "session", "session.db")
            
            # 从 all_keys.json 获取 session.db 的专用密钥
            keys_file = os.path.join(os.path.dirname(__file__), 'wechat-decrypt', 'all_keys.json')
            if os.path.exists(keys_file):
                import json
                with open(keys_file, 'r', encoding='utf-8') as f:
                    all_keys = json.load(f)
                
                # 获取 session.db 的密钥
                session_key_info = all_keys.get('session\\session.db', {})
                self.session_key = session_key_info.get('enc_key', self.key)
                self.log(f"session.db 密钥：{self.session_key[:20]}...")
            else:
                self.session_key = self.key
                self.log("未找到 all_keys.json，使用 message.db 的密钥")
            
            if not self.key or not self.db_path:
                raise Exception("获取密钥或路径失败")
            
            self.log(f"微信环境初始化成功：{self.wx_info.get('wxid')}")
            return True
        except Exception as e:
            self.log(f"初始化失败：{str(e)}")
            return False

    def load_contacts(self):
        if not self.micro_db_path or not os.path.exists(self.micro_db_path):
            return
        try:
            decrypt(self.key, self.micro_db_path, self.micro_backup)
            conn = sqlite3.connect(self.micro_backup)
            cursor = conn.cursor()
            
            # 微信 4.x: 加载联系人
            cursor.execute("SELECT username, nick_name, remark FROM contact WHERE username IS NOT NULL;")
            for row in cursor.fetchall():
                uname, nick, remark = row
                name = remark if remark else nick
                if name and uname:
                    # 存储 username -> name 的映射
                    self.contact_map[uname] = name
            
            # 保存 cursor 用于后续查询
            self.contact_cursor = cursor
            self.contact_conn = conn
            
            self.log(f"联系人加载完成：{len(self.contact_map)} 个")
            
            # 初始化 session 状态（避免历史消息重复推送）
            self.init_session_state()
            
        except Exception as e:
            self.log(f"加载联系人失败：{str(e)}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
    
    def init_session_state(self):
        """初始化 session 状态，避免历史消息重复推送"""
        if not self.session_db_path or not os.path.exists(self.session_db_path):
            return
        
        try:
            # 解密 session.db
            from wx_decrypt import decrypt as wx_decrypt
            session_backup = os.path.join(os.path.dirname(self.db_backup), "session_init.db")
            wx_decrypt(self.key, self.session_db_path, session_backup)
            
            # 查询当前状态
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
            
            self.log(f"已初始化 session 状态，跟踪 {len(self.prev_session_state)} 个会话")
        
        except Exception as e:
            self.log(f"初始化 session 状态失败：{str(e)}")

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
            from wx_decrypt import decrypt as wx_decrypt
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
            
            cursor.execute("""
                SELECT username, unread_count, summary, last_timestamp,
                       last_msg_type, last_msg_sender, last_sender_display_name
                FROM SessionTable WHERE last_timestamp > 0
            """)
            
            total_count = 0
            for row in cursor.fetchall():
                username, unread, summary, timestamp, msg_type, sender, sender_name = row
                
                # 检查是否是新消息
                if username in self.prev_session_state:
                    prev = self.prev_session_state[username]
                    if timestamp <= prev['timestamp']:
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
                
                self.send_notification(display, notification_text, timestamp)
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
        """主动轮询模式（直接调用 monitor_web.py 的函数）"""
        if not self.session_db_path or not os.path.exists(self.session_db_path):
            self.log("session.db 不存在")
            return
        
        wal_path = self.session_db_path + "-wal"
        
        # 使用 session.db 的专用密钥
        if isinstance(self.session_key, str):
            enc_key = bytes.fromhex(self.session_key)
        else:
            enc_key = self.session_key
        
        # 导入 monitor_web.py 的函数
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'wechat-decrypt'))
        from monitor_web import full_decrypt, decrypt_wal_full
        
        # 初始全量解密 + WAL patch
        self.log("初始解密 session.db...")
        t0 = time.time()
        try:
            pages, ms = full_decrypt(self.session_db_path, self.session_decrypted_path, enc_key)
            self.log(f"full_decrypt 返回：{pages}页，{ms:.1f}ms")
            
            wal_patched, wal_ms = decrypt_wal_full(wal_path, self.session_decrypted_path, enc_key)
            t1 = time.time()
            self.log(f"初始解密完成：{(t1-t0)*1000:.1f}ms, WAL patch {wal_patched}页")
            
            # 验证解密后的文件
            if os.path.exists(self.session_decrypted_path):
                sz = os.path.getsize(self.session_decrypted_path)
                self.log(f"解密后的文件大小：{sz} bytes")
                
                # 尝试用 SQLite 验证
                try:
                    test_conn = sqlite3.connect(f"file:{self.session_decrypted_path}?mode=ro", uri=True)
                    test_conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
                    test_conn.close()
                    self.log("✓ 解密后的数据库文件验证成功")
                except Exception as verify_err:
                    self.log(f"✗ 解密后的数据库验证失败：{verify_err}")
        except Exception as e:
            self.log(f"初始解密失败：{e}")
            import traceback
            self.log(f"错误详情：{traceback.format_exc()}")
            return
        
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
    
    def process_and_push(self):
        """查询并推送（完全参考 monitor_web.py，零延迟）"""
        if not os.path.exists(self.session_decrypted_path):
            self.log(f"session 解密文件不存在：{self.session_decrypted_path}")
            return
        
        try:
            # 使用只读模式查询（避免锁冲突）
            self.log(f"开始查询 session 数据库...")
            conn = sqlite3.connect(f"file:{self.session_decrypted_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT username, unread_count, summary, last_timestamp,
                       last_msg_type, last_msg_sender, last_sender_display_name
                FROM SessionTable WHERE last_timestamp > 0
            """)
            
            rows = cursor.fetchall()
            conn.close()
            self.log(f"查询到 {len(rows)} 个会话")
            
            # 收集所有新消息
            new_msgs = []
            for row in rows:
                username, unread, summary, timestamp, msg_type, sender, sender_name = row
                
                # 检查是否是新消息
                if username in self.prev_session_state:
                    prev = self.prev_session_state[username]
                    if timestamp <= prev['timestamp']:
                        continue
                
                # 获取聊天显示名称
                display = self.contact_map.get(username, username)
                is_group = '@chatroom' in username
                
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
                
                self.log(f"准备推送：{display} - {notification_text[:50]}")
                self.send_notification(display, notification_text, timestamp)
                new_msgs.append(notification_text)
                
                # 更新状态
                self.prev_session_state[username] = {
                    'timestamp': timestamp,
                    'msg_type': msg_type,
                }
            
            if new_msgs:
                self.log(f"推送 {len(new_msgs)} 条消息")
                # 发送消息数量信号
                self.msg_count_signal.emit(len(new_msgs))
                # 发送最后一条消息信号
                if new_msgs:
                    self.last_msg_signal.emit(new_msgs[-1][:20])
            else:
                self.log("没有新消息")
        
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
        if not summary:
            return ""
        
        # 根据消息类型显示
        if msg_type == 1:  # 文本
            return summary[:50] + "..." if len(summary) > 50 else summary
        elif msg_type == 3:  # 图片
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
            return summary
        else:
            return summary[:50] + "..." if len(summary) > 50 else summary
    
    def send_notification(self, sender, content, create_time):
        if not self.config.get('enable_notify', True):
            return
            
        time_str = datetime.fromtimestamp(create_time).strftime('%H:%M:%S')
        title = f"{sender}"
        msg = f"{content[:50]}..." if len(content) > 50 else f"{content}"
        
        # 使用 winotify 发送 Windows 10/11 通知
        if HAS_WINOTIFY:
            try:
                # 获取图标路径（相对于项目根目录）
                icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.png')
                
                toast = Notification(
                    app_id="微信消息",
                    title=title,
                    msg=msg,
                    icon=icon_path
                )
                toast.show()
                self.log(f"winotify 通知已发送：{title} - {msg}")
            except Exception as e:
                self.log(f"winotify 通知发送失败：{str(e)}")
                import traceback
                self.log(f"错误详情：{traceback.format_exc()}")
        else:
            self.log(f"通知：{title} - {msg}")

    def run(self):
        self.running = True
        self.status_signal.emit("running")
        
        if not self.init_wx_env():
            self.status_signal.emit("error")
            return

        self.load_contacts()
        
        self.log("正在初始同步...")
        # 初始同步只需要加载联系人和 session 状态，不需要解密 message 数据库
        self.log("初始同步完成")
        
        # 使用主动轮询模式（参考 wechat-decrypt，30ms 延迟）
        self.log("启动轮询监听（30ms 间隔）...")
        self.run_polling()
        
        self.status_signal.emit("stopped")
        self.log("监听服务已停止")
        self.finished.emit()

    def stop(self):
        self.running = False
        # 关闭数据库连接
        if hasattr(self, 'contact_conn'):
            try:
                self.contact_conn.close()
            except:
                pass

# --- UI 界面部分 ---

class Interface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName('Interface')
        self.worker = None
        self.thread = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        self.setting_group = SettingCardGroup("运行配置", self)
        
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
        self.dir_temp_path = os.path.join(os.getcwd(), "wx_temp_data")
        self.dir_card.setContent(self.dir_temp_path)
        self.dir_card.clicked.connect(self.choose_dir)
        self.setting_group.addSettingCard(self.dir_card)
        
        # 3. 防抖动时间 (使用自定义 SliderSettingCard)
        self.debounce_card = SliderSettingCard(
            1000, 5000, 100, 1000, 
            FIF.HISTORY, 
            "消息防抖动 (ms)", 
            "避免微信连续写入导致重复解密 (推荐 1000ms)"
        )
        self.setting_group.addSettingCard(self.debounce_card)
        
        # 4. 通知停留时间 (使用自定义 SliderSettingCard)
        self.duration_card = SliderSettingCard(
            1, 30, 1, 5,
            FIF.INFO,
            "通知停留时间 (秒)",
            "Windows 通知显示的持续时间"
        )
        self.setting_group.addSettingCard(self.duration_card)
        
        layout.addWidget(self.setting_group)
        
        # 状态面板
        self.status_group = SettingCardGroup("实时监控", self)
        
        # 创建状态卡片
        status_card = SettingCard(FIF.INFO, "监控状态", "实时显示服务运行情况", self.status_group)
        
        # 创建一个容器 widget 来容纳所有状态信息
        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setSpacing(20)
        status_layout.setContentsMargins(16, 16, 16, 16)
        
        # 状态信息
        self.status_label = BodyLabel("当前状态：未运行")
        self.status_label.setTextColor(QColor(100, 100, 100), QColor(200, 200, 200))  # 浅色主题，深色主题
        status_layout.addWidget(self.status_label)
        
        # 添加到卡片布局
        status_card.layout().addWidget(status_widget)
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
        
        # 设置布局权重，让日志区域可以随窗口大小调整
        layout.addWidget(self.log_text, 1)
        
        layout.addStretch()
        self.setLayout(layout)

    def choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择缓存目录", self.dir_temp_path)
        if path:
            self.dir_temp_path = path
            self.dir_card.setContent(path)

    def toggle_service(self, checked):
        if checked:
            self.start_service()
        else:
            self.stop_service()

    def start_service(self):
        import logging
        logging.info("开始启动服务...")
        
        config = {
            'temp_dir': self.dir_temp_path,
            'debounce_time': self.debounce_card.value / 1000.0,
            'notify_duration': self.duration_card.value,
            'enable_notify': True
        }
        
        self.worker = WeChatMonitorWorker(config)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        
        self.worker.log_signal.connect(self.update_log)
        self.worker.status_signal.connect(self.update_status)
        # 移除了消息统计信号，因为 UI 中不再显示
        # self.worker.msg_count_signal.connect(self.update_count)
        # self.worker.last_msg_signal.connect(self.update_last_msg)
        # 移除了 notify_signal 连接，因为现在只使用 Windows 系统通知
        
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
            self.worker.stop()
            if self.thread and self.thread.isRunning():
                logging.info("等待线程退出...")
                self.thread.quit()
                self.thread.wait(3000)
                logging.info("线程已退出")
            self.worker = None
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

    def update_count(self, count):
        current_text = self.count_label.text()
        try:
            prev = int(current_text.split(": ")[1])
        except:
            prev = 0
        self.count_label.setText(f"已处理消息：{prev + count}")

    def update_last_msg(self, msg):
        self.last_msg_label.setText(f"最后消息：{msg}")

class MainWindow(FluentWidget):
    def __init__(self):
        # 必须先初始化父类，Mica 效果会在父类初始化中自动应用
        super().__init__()

        # 自动跟随系统主题（深色/浅色）
        from qfluentwidgets import Theme, setTheme
        setTheme(Theme.AUTO)

        # 创建主题监听器
        self.themeListener = SystemThemeListener(self)

        # 设置窗口图标和标题
        icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.ico')
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("微信消息通知助手")
        self.resize(600, 700)

        # 顶层布局改为垂直布局，结构更清晰
        self.main_layout = QVBoxLayout(self)
        # 留出标题栏的空间
        self.main_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        self.main_layout.setSpacing(10)

        # 创建并添加接口
        self.interface = Interface(self)
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

        # 连接主题变化信号，确保界面能响应系统主题变化
        qconfig.themeChanged.connect(self.on_theme_changed)

        # 启动主题监听器
        self.themeListener.start()
        
        # 初始化系统托盘
        self._init_system_tray(icon_path)
    
    def on_theme_changed(self):
        """主题变化时的回调函数"""
        pass
    
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
        if self.interface.worker:
            self.interface.stop_service()
        self.tray_icon.hide()
        QApplication.quit()
    
    def closeEvent(self, event):
        """处理窗口关闭事件 - 最小化到托盘而不是退出"""
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
