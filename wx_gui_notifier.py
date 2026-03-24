import sys
import os
import time
import sqlite3
import logging
import threading
import ctypes
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
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QSlider, QSpinBox, QLabel

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
        
        self.temp_dir = config.get('temp_dir', './temp_data')
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        self.db_backup = os.path.join(self.temp_dir, "msg_decrypted.db")
        self.micro_backup = os.path.join(self.temp_dir, "micro_decrypted.db")

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
            
            # 微信 4.x: 尝试多种方式加载联系人
            # 1. 首先尝试使用 id 字段（整数类型）
            cursor.execute("SELECT id, username, nick_name, remark FROM contact WHERE id IS NOT NULL;")
            for row in cursor.fetchall():
                uid, uname, nick, remark = row
                name = remark if remark else nick
                if name: 
                    # 同时使用整数和字符串形式存储
                    self.contact_map[int(uid)] = name
                    self.contact_map[str(uid)] = name
            conn.close()
            
            self.log(f"联系人加载完成：{len(self.contact_map)} 个")
        except Exception as e:
            self.log(f"加载联系人失败：{str(e)}")
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
        if not self.decrypt_msg_db():
            self.log("解密失败，跳过处理")
            return

        try:
            self.log("开始处理消息...")
            conn = sqlite3.connect(self.db_backup)
            cursor = conn.cursor()
            
            # 微信 4.x: 获取所有 Msg_开头的表
            self.log(f"查询消息表...")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
            msg_tables = [row[0] for row in cursor.fetchall()]
            self.log(f"找到 {len(msg_tables)} 个消息表：{msg_tables}")
            
            if not msg_tables:
                conn.close()
                return
            
            total_count = 0
            last_sender = ""
            
            # 查询所有消息表
            for table_name in msg_tables:
                try:
                    # 每个表有独立的 local_id 序列
                    table_last_id = self.last_local_id_map.get(table_name, 0)
                    self.log(f"查询表 {table_name}，last_local_id={table_last_id}")
                    # 微信 4.x 字段：local_id, real_sender_id, create_time, message_content, compress_content
                    query = f"""
                        SELECT local_id, real_sender_id, create_time, message_content, compress_content
                        FROM {table_name}
                        WHERE local_id > ?
                        ORDER BY local_id ASC
                    """
                    cursor.execute(query, (table_last_id,))
                    rows = cursor.fetchall()
                    self.log(f"表 {table_name} 查询到 {len(rows)} 条新消息")
                    
                    for row in rows:
                        local_id, real_sender_id, create_time, content, compress_content = row
                        self.log(f"找到新消息 local_id={local_id}, sender={real_sender_id}")
                        
                        # 尝试解压/解密消息内容
                        msg_text = self.extract_message_content(content, compress_content)
                        if not msg_text:
                            self.log(f"消息内容为空，跳过")
                            continue
                        
                        # 获取发送者名称 - real_sender_id 可能是整数或字符串
                        sender_name = self.contact_map.get(real_sender_id)
                        
                        # 如果找不到，尝试转换为字符串查找
                        if not sender_name and real_sender_id is not None:
                            sender_name = self.contact_map.get(str(real_sender_id))
                        
                        if not sender_name:
                            # 如果联系表中找不到，尝试更详细的日志
                            self.log(f"⚠️ 未找到联系人 ID={real_sender_id} (type={type(real_sender_id).__name__})，使用 ID 显示")
                            sender_name = f"ID:{real_sender_id}" if real_sender_id else "未知"
                        
                        self.send_notification(sender_name, msg_text, create_time)
                        last_sender = sender_name
                        total_count += 1
                    
                    # 更新该表的最大 ID
                    if rows:
                        cursor.execute(f"SELECT MAX(local_id) FROM {table_name}")
                        result = cursor.fetchone()
                        if result and result[0]:
                            self.last_local_id_map[table_name] = result[0]
                            self.log(f"表 {table_name} 更新最大 ID 为 {result[0]}")
                            
                except Exception as e:
                    import traceback
                    self.log(f"查询表 {table_name} 失败：{str(e)}")
                    self.log(f"错误详情：{traceback.format_exc()}")
                    continue
            
            conn.close()
            
            self.log(f"本轮处理完成，共 {total_count} 条新消息，last_local_id_map 更新为 {self.last_local_id_map}")
            
            if total_count > 0:
                self.msg_count_signal.emit(total_count)
                self.last_msg_signal.emit(f"{last_sender}: ...")
                self.log(f"处理了 {total_count} 条新消息")
                
        except Exception as e:
            self.log(f"处理消息出错：{str(e)}")
    
    def extract_message_content(self, content, compress_content):
        """提取消息文本内容（微信 4.x）"""
        try:
            # 如果是明文，直接返回
            if content and isinstance(content, str):
                return content.strip()
            
            # 尝试从 compress_content 提取
            if compress_content:
                if isinstance(compress_content, str) and compress_content.strip():
                    return compress_content.strip()
            
            # 如果是二进制数据，尝试简单处理
            if isinstance(content, bytes):
                # 尝试 UTF-8 解码
                try:
                    text = content.decode('utf-8', errors='ignore').strip()
                    if text:
                        return text
                except:
                    pass
            
            return ""
        except Exception as e:
            self.log(f"提取消息内容失败：{str(e)}")
            return ""


    def send_notification(self, sender, content, create_time):
        if not self.config.get('enable_notify', True):
            return
            
        time_str = datetime.fromtimestamp(create_time).strftime('%H:%M:%S')
        title = "微信"
        msg = f"{sender}: {content[:50]}..." if len(content) > 50 else f"{sender}: {content}"
        
        # 使用 winotify 发送 Windows 10/11 通知
        if HAS_WINOTIFY:
            try:
                # 获取图标路径（相对于项目根目录）
                icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.png')
                
                toast = Notification(
                    app_id="微信消息通知",
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
        if self.decrypt_msg_db():
            try:
                conn = sqlite3.connect(self.db_backup)
                cursor = conn.cursor()
                # 微信 4.x: 获取所有 Msg_开头的表并找到最大 local_id
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                # 微信 4.x: 每个表有独立的 local_id 序列，需要分别记录
                self.last_local_id_map = {}  # table_name -> max_local_id
                msg_tables = [row[0] for row in cursor.fetchall()]
                for table_name in msg_tables:
                    try:
                        cursor.execute(f"SELECT MAX(local_id) FROM {table_name}")
                        result = cursor.fetchone()
                        self.last_local_id_map[table_name] = result[0] if result and result[0] else 0
                    except:
                        self.last_local_id_map[table_name] = 0
                self.log(f"初始同步完成，各表最大 ID: {self.last_local_id_map}")
                conn.close()
                self.log(f"初始同步完成，ID: {self.last_local_id}")
            except: pass
        else:
            self.status_signal.emit("error")
            return

        class Handler(FileSystemEventHandler):
            def __init__(self, worker):
                self.worker = worker
                self.last_trigger = 0
                self.lock = threading.Lock()
                self.debounce = float(self.worker.config.get('debounce_time', 1.0))

            def on_modified(self, event):
                # 记录所有文件变化
                self.worker.log(f"📁 文件变化：{event.src_path}")
                if event.is_directory:
                    return
                
                now = time.time()
                with self.lock:
                    if now - self.last_trigger < self.debounce:
                        self.worker.log(f"  ⏱️ 防抖动跳过")
                        return
                    self.last_trigger = now
                
                self.worker.log(f"  ✅ 触发消息处理")
                threading.Thread(target=self._delayed_process, daemon=True).start()

            def _delayed_process(self):
                time.sleep(0.3)
                if self.worker.running:
                    self.worker.process_messages()

        event_handler = Handler(self)
        self.observer = Observer()
        self.observer.schedule(event_handler, path=os.path.dirname(self.db_path), recursive=False)
        self.observer.start()
        self.log("监听服务已启动")

        while self.running:
            time.sleep(1)
        
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.status_signal.emit("stopped")
        self.log("监听服务已停止")
        self.finished.emit()

    def stop(self):
        self.running = False

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
        
        # 消息统计
        self.count_label = BodyLabel("已处理消息：0")
        self.count_label.setTextColor(QColor(100, 100, 100), QColor(200, 200, 200))  # 浅色主题，深色主题
        status_layout.addWidget(self.count_label)
        
        # 最后消息
        self.last_msg_label = BodyLabel("最后消息：无")
        self.last_msg_label.setTextColor(QColor(100, 100, 100), QColor(200, 200, 200))  # 浅色主题，深色主题
        status_layout.addWidget(self.last_msg_label)
        
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
        self.worker.msg_count_signal.connect(self.update_count)
        self.worker.last_msg_signal.connect(self.update_last_msg)
        # 移除了 notify_signal 连接，因为现在只使用 Windows 系统通知
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        
        self.thread.start()
        InfoBar.success("服务已启动", "开始监听微信消息...", position=InfoBarPosition.TOP, parent=self)

    def stop_service(self):
        if self.worker:
            self.worker.stop()
            if self.thread and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(3000)
            self.worker = None
            self.thread = None
        InfoBar.warning("服务已停止", "监听已关闭", position=InfoBarPosition.TOP, parent=self)
        self.update_status("stopped")

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

    def on_theme_changed(self):
        """主题变化时的回调函数"""
        pass

    def closeEvent(self, event):
        if self.interface.worker:
            self.interface.stop_service()
        event.accept()

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    
    # 设置应用图标
    icon_path = os.path.join(os.path.dirname(__file__), 'src', 'img', 'WeChat.ico')
    app.setWindowIcon(QIcon(icon_path))
    
    from qfluentwidgets import Theme, setTheme
    setTheme(Theme.AUTO)
    
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
