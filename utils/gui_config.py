"""
GUI 配置管理模块

自动检测微信数据目录，管理 GUI 配置文件
首次运行时自动创建配置文件，配置文件损坏时自动重新生成
"""
import json
import os
import sys
import glob
import platform
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("WeChatNotifier")

# 配置文件路径
# 对于打包的程序，使用 AppData 目录而不是临时目录
if getattr(sys, 'frozen', False):
    # 打包后的程序，使用 AppData\Local\WxGuiNotifier 目录
    CONFIG_FILE = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'WxGuiNotifier', 'gui_config.json')
else:
    # 开发环境，使用 utils 目录
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_config.json")

CONFIG_EXAMPLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_config.example.json")

# 系统检测
_SYSTEM = platform.system().lower()

# 默认配置模板
if _SYSTEM == "windows":
    _DEFAULT_TEMPLATE_DIR = r"D:\xwechat_files\your_wxid\db_storage"
    _DEFAULT_PROCESS = "Weixin.exe"
elif _SYSTEM == "darwin":
    _DEFAULT_TEMPLATE_DIR = os.path.expanduser("~/Documents/xwechat_files/your_wxid/db_storage")
    _DEFAULT_PROCESS = "WeChat"
elif _SYSTEM == "linux":
    _DEFAULT_TEMPLATE_DIR = os.path.expanduser("~/Documents/xwechat_files/your_wxid/db_storage")
    _DEFAULT_PROCESS = "wechat"
else:
    _DEFAULT_TEMPLATE_DIR = ""
    _DEFAULT_PROCESS = ""

_DEFAULT_GUI_CONFIG = {
    "db_dir": _DEFAULT_TEMPLATE_DIR,
    "keys_file": "all_keys.json",
    "decrypted_dir": "wx_decrypted",
    "decoded_image_dir": "decoded_images",
    "wechat_process": _DEFAULT_PROCESS,
    # GUI 特有配置
    "gui": {
        "temp_dir": "wx_temp_data",
        "debounce_time_ms": 1000,
        "notify_duration_sec": 5,
        "enable_notify": True,
        "minimize_to_tray": True,
        "auto_start_service": False,
        # 消息过滤（默认开启，避免免打扰/公众号文章打扰）
        "filter_mute": True,
        "filter_official_article": True,
    }
}


def _choose_candidate(candidates: list) -> Optional[str]:
    """在多个候选目录中选择一个（自动选择最新的）"""
    if len(candidates) == 1:
        return candidates[0]
    
    if len(candidates) > 1:
        # GUI 模式下自动选择最新的目录（按 message 目录 mtime）
        def _mtime(path):
            msg_dir = os.path.join(path, "message")
            target = msg_dir if os.path.isdir(msg_dir) else path
            try:
                return os.path.getmtime(target)
            except OSError:
                return 0
        
        candidates.sort(key=_mtime, reverse=True)
        logger.info(f"检测到多个微信数据目录，自动选择最新的：{candidates[0]}")
        return candidates[0]
    
    return None


def scan_all_wechat_dirs() -> list:
    """扫描所有微信数据目录，返回候选列表
    
    Returns:
        list: 微信数据目录路径列表（包含详细信息的字典）
    """
    if _SYSTEM == "windows":
        return _scan_windows()
    elif _SYSTEM == "linux":
        return _scan_linux()
    return []


def _scan_windows() -> list:
    """Windows 下扫描所有微信数据目录"""
    appdata = os.environ.get("APPDATA", "")
    config_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
    
    if not os.path.isdir(config_dir):
        logger.debug(f"微信配置目录不存在：{config_dir}")
        return []
    
    # 从 ini 文件中找到有效的目录路径
    data_roots = []
    for ini_file in glob.glob(os.path.join(config_dir, "*.ini")):
        try:
            # 微信 ini 可能是 utf-8 或 gbk 编码
            content = None
            for enc in ("utf-8", "gbk"):
                try:
                    with open(ini_file, "r", encoding=enc) as f:
                        content = f.read(1024).strip()
                    break
                except UnicodeDecodeError:
                    continue
            
            if not content or any(c in content for c in "\n\r\x00"):
                continue
            
            if os.path.isdir(content):
                data_roots.append(content)
        except OSError:
            continue
    
    # 在每个根目录下搜索 xwechat_files\*\db_storage
    seen = set()
    candidates = []
    for root in data_roots:
        pattern = os.path.join(root, "xwechat_files", "*", "db_storage")
        for match in glob.glob(pattern):
            normalized = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and normalized not in seen:
                seen.add(normalized)
                # 提取 wxid 作为显示名称
                wxid = os.path.basename(os.path.dirname(match))
                
                # 尝试读取用户昵称
                display_name = _get_user_display(match, wxid)
                
                candidates.append({
                    'path': match,
                    'wxid': wxid,
                    'display': display_name,
                    'mtime': _get_dir_mtime(match)
                })
    
    # 按 mtime 降序排序（最新的在前）
    candidates.sort(key=lambda x: x['mtime'], reverse=True)
    return candidates


def _scan_linux() -> list:
    """Linux 下扫描所有微信数据目录"""
    seen = set()
    candidates = []
    search_roots = [
        os.path.expanduser("~/Documents/xwechat_files"),
    ]
    
    # sudo 运行时回退到实际用户
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        try:
            sudo_home = pwd.getpwnam(sudo_user).pw_dir
            fallback = os.path.join(sudo_home, "Documents", "xwechat_files")
            if fallback not in search_roots:
                search_roots.append(fallback)
        except KeyError:
            pass
    
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        pattern = os.path.join(root, "*", "db_storage")
        for match in glob.glob(pattern):
            normalized = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and normalized not in seen:
                seen.add(normalized)
                wxid = os.path.basename(os.path.dirname(match))
                candidates.append({
                    'path': match,
                    'wxid': wxid,
                    'display': f"微信账号：{wxid}",
                    'mtime': _get_dir_mtime(match)
                })
    
    # 早期版本路径
    old_path = os.path.expanduser("~/.local/share/weixin/data/db_storage")
    if os.path.isdir(old_path):
        normalized = os.path.normcase(os.path.normpath(old_path))
        if normalized not in seen:
            seen.add(normalized)
            candidates.append({
                'path': old_path,
                'wxid': 'default',
                'display': '微信账号（旧版）',
                'mtime': _get_dir_mtime(old_path)
            })
    
    candidates.sort(key=lambda x: x['mtime'], reverse=True)
    return candidates


def _get_dir_mtime(path: str) -> float:
    """获取目录的修改时间（优先使用 message 子目录）"""
    msg_dir = os.path.join(path, "message")
    target = msg_dir if os.path.isdir(msg_dir) else path
    try:
        return os.path.getmtime(target)
    except OSError:
        return 0


def _get_user_display(db_storage_path: str, wxid: str) -> str:
    """
    尝试从配置文件中读取用户昵称，如果失败则返回 wxid
    
    Args:
        db_storage_path: db_storage 目录路径
        wxid: 微信 ID
        
    Returns:
        str: 显示名称（昵称或 wxid）
    """
    try:
        # 尝试从 gui_config.json 中读取该用户的昵称
        gui_config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gui_config.json")
        if os.path.exists(gui_config_file):
            import json
            with open(gui_config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 如果当前配置的 wxid 匹配，使用昵称
            if config.get('wxid') == wxid and config.get('nickname'):
                nickname = config.get('nickname', wxid)
                return f"{nickname} ({wxid})"
        
        # 默认返回格式化的 wxid
        return f"微信账号：{wxid}"
        
    except Exception as e:
        logger.debug(f"读取用户信息失败：{e}")
        return f"微信账号：{wxid}"


def auto_detect_db_dir() -> Optional[str]:
    """自动检测微信数据目录（单个）"""
    candidates = scan_all_wechat_dirs()
    if candidates:
        return candidates[0]['path']
    return None


def check_wechat_running(process_name: str = None) -> bool:
    """检查微信是否在运行"""
    if process_name is None:
        process_name = _DEFAULT_PROCESS
    
    # 尝试使用 psutil
    try:
        import psutil
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] and process_name.lower() in proc.info['name'].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        # psutil 不可用时，使用 ctypes 枚举进程（仅 Windows）
        if _SYSTEM == "windows":
            import ctypes
            from ctypes import wintypes
            
            # 创建快照
            h_snapshot = ctypes.windll.kernel32.CreateToolhelp32SnapshotW(0x00000002, 0)  # TH32CS_SNAPPROCESS
            if h_snapshot == -1:
                return False
            
            try:
                # 遍历进程
                class PROCESSENTRY32W(ctypes.Structure):
                    _fields_ = [
                        ('dwSize', wintypes.DWORD),
                        ('cntUsage', wintypes.DWORD),
                        ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.POINTER(wintypes.ULONG)),
                        ('th32ModuleID', wintypes.DWORD),
                        ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD),
                        ('pcPriClassBase', wintypes.LONG),
                        ('dwFlags', wintypes.DWORD),
                        ('szExeFile', wintypes.WCHAR * 260)
                    ]
                
                pe32 = PROCESSENTRY32W()
                pe32.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                
                if not ctypes.windll.kernel32.Process32FirstW(h_snapshot, ctypes.byref(pe32)):
                    return False
                
                while True:
                    if process_name.lower() in pe32.szExeFile.lower():
                        return True
                    if not ctypes.windll.kernel32.Process32NextW(h_snapshot, ctypes.byref(pe32)):
                        break
                
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(h_snapshot)
        else:
            # 其他系统，简单检查
            import subprocess
            try:
                result = subprocess.run(['pgrep', '-f', process_name], capture_output=True)
                return result.returncode == 0
            except:
                return False


def ensure_config_file() -> Tuple[Dict, bool]:
    """确保配置文件存在且有效
    
    Returns:
        Tuple[Dict, bool]: (配置字典，是否为新创建)
    """
    config = {}
    is_new = False
    
    # 尝试加载现有配置
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info(f"已加载配置文件：{CONFIG_FILE}")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"配置文件格式损坏：{e}，将重新生成")
            is_new = True
    else:
        logger.info("配置文件不存在，将创建新配置")
        is_new = True
    
    # 如果配置为空或损坏，使用默认配置
    if not config:
        config = _DEFAULT_GUI_CONFIG.copy()
        config["gui"] = _DEFAULT_GUI_CONFIG["gui"].copy()
    
    # 检查 db_dir 是否需要更新
    db_dir = config.get("db_dir", "")
    needs_update = False
    
    # db_dir 缺失、为模板值、或包含 your_wxid 时，尝试自动检测
    if not db_dir or db_dir == _DEFAULT_TEMPLATE_DIR or "your_wxid" in db_dir:
        detected = auto_detect_db_dir()
        if detected:
            logger.info(f"自动检测到微信数据目录：{detected}")
            config["db_dir"] = detected
            needs_update = True
        else:
            if not db_dir or db_dir == _DEFAULT_TEMPLATE_DIR:
                logger.warning("未能自动检测微信数据目录")
                logger.warning(f"请手动编辑 {CONFIG_FILE} 中的 db_dir 字段")
                if _SYSTEM == "linux":
                    logger.warning("Linux 默认路径类似：~/Documents/xwechat_files/<wxid>/db_storage")
                else:
                    logger.warning("路径可在 微信设置 → 文件管理 中找到")
    
    # 检查 keys_file 路径
    # 始终使用相对路径，避免打包后路径错误
    if "keys_file" not in config:
        config["keys_file"] = "all_keys.json"
        needs_update = True
    elif os.path.isabs(config["keys_file"]):
        # 如果已经是绝对路径，转为相对路径
        config["keys_file"] = "all_keys.json"
        needs_update = True
    
    # 检查其他必要字段
    for key in ["decrypted_dir", "decoded_image_dir"]:
        if key not in config:
            config[key] = _DEFAULT_GUI_CONFIG[key]
            needs_update = True
    
    # 检查 GUI 配置
    if "gui" not in config:
        config["gui"] = _DEFAULT_GUI_CONFIG["gui"].copy()
        needs_update = True
    else:
        # 补充 GUI 配置中缺失的字段
        for gui_key, gui_value in _DEFAULT_GUI_CONFIG["gui"].items():
            if gui_key not in config["gui"]:
                config["gui"][gui_key] = gui_value
                needs_update = True
    
    # 如果需要更新，保存配置
    if needs_update or is_new:
        save_config(config)
        logger.info(f"配置已保存到：{CONFIG_FILE}")
    
    # 将相对路径转为绝对路径
    # 对于打包的程序，使用程序所在目录作为基准
    if getattr(sys, 'frozen', False):
        # 打包后的程序，使用程序所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发环境，使用 utils 目录的父目录
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    for key in ["keys_file", "decrypted_dir", "decoded_image_dir"]:
        if key in config and not os.path.isabs(config[key]):
            config[key] = os.path.join(base_dir, config[key])
    
    # GUI temp_dir 转为绝对路径
    if "temp_dir" in config["gui"] and not os.path.isabs(config["gui"]["temp_dir"]):
        config["gui"]["temp_dir"] = os.path.join(base_dir, config["gui"]["temp_dir"])
    
    # 自动推导微信数据根目录
    db_dir = config.get("db_dir", "")
    if db_dir and os.path.basename(db_dir) == "db_storage":
        config["wechat_base_dir"] = os.path.dirname(db_dir)
    else:
        config["wechat_base_dir"] = db_dir
    
    return config, is_new


def save_config(config: Dict) -> bool:
    """保存配置到文件
    
    Args:
        config: 配置字典
        
    Returns:
        bool: 是否保存成功
    """
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        
        logger.info(f"配置已保存：{CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"保存配置失败：{e}")
        return False


def create_example_config() -> str:
    """创建配置示例文件
    
    Returns:
        str: 示例文件路径
    """
    try:
        with open(CONFIG_EXAMPLE_FILE, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_GUI_CONFIG, f, indent=4, ensure_ascii=False)
        
        logger.info(f"配置示例已创建：{CONFIG_EXAMPLE_FILE}")
        return CONFIG_EXAMPLE_FILE
    except Exception as e:
        logger.error(f"创建配置示例失败：{e}")
        return ""


def validate_keys_file(keys_file: str) -> bool:
    """验证密钥文件是否存在且有效
    
    Args:
        keys_file: 密钥文件路径
        
    Returns:
        bool: 是否有效
    """
    if not os.path.exists(keys_file):
        logger.warning(f"密钥文件不存在：{keys_file}")
        return False
    
    try:
        with open(keys_file, "r", encoding="utf-8") as f:
            keys = json.load(f)
        
        # 检查是否有有效的密钥（排除元数据）
        valid_keys = {k: v for k, v in keys.items() if not k.startswith("_")}
        
        if not valid_keys:
            logger.warning("密钥文件为空或不包含有效密钥")
            return False
        
        logger.info(f"密钥文件验证成功：{len(valid_keys)} 个密钥")
        return True
    
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"密钥文件格式损坏：{e}")
        return False


def get_gui_config() -> Dict:
    """获取 GUI 配置（便捷函数）
    
    Returns:
        Dict: GUI 配置字典
    """
    config, _ = ensure_config_file()
    return config.get("gui", {})


# 模块加载时自动确保配置文件存在
if __name__ != "__main__":
    # 作为模块导入时不自动执行，由主程序调用
    pass
