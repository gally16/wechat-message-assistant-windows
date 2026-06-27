"""
微信数据库解密模块（完全独立版）

不依赖 wechat-decrypt 目录，所有功能都集成在此文件中
支持微信 4.x 版本

修复说明（exe 模式兼容）：
- 打包成 onefile exe 后，__file__ 指向 _MEIxxxxxx 临时解压目录，
  原代码用 os.path.dirname(__file__) 定位 all_keys.json 和 gui_config.json
  全部失效，导致「微信环境初始化失败」。
- 现统一通过 _get_base_dir() / _get_config_file() 解析正确路径：
    exe 模式：exe 所在目录 + LOCALAPPDATA/WxGuiNotifier/gui_config.json
    开发模式：项目根目录 + utils/gui_config.json
"""

import os
import sys
import json
import logging
import subprocess
from typing import List, Dict

logger = logging.getLogger("WeChatNotifier")

# 常量定义
PAGE_SZ = 4096


# ----------------------------------------------------------------------
# 路径解析辅助函数（exe 模式兼容）
# ----------------------------------------------------------------------
def _is_frozen() -> bool:
    """是否处于 PyInstaller 打包后的 exe 模式"""
    return getattr(sys, 'frozen', False)


def _get_base_dir() -> str:
    """获取基础目录

    - exe 模式：exe 所在目录（如 D:\\Program Files (x86)\\Tencent\\Weixin）
    - 开发模式：项目根目录（core 的父目录）
    """
    if _is_frozen():
        return os.path.dirname(sys.executable)
    # 开发模式：core/wx_decrypt.py 的父目录的父目录 = 项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_config_file() -> str:
    """获取 GUI 配置文件路径，与 utils.gui_config.CONFIG_FILE 保持一致

    - exe 模式：%LOCALAPPDATA%\\WxGuiNotifier\\gui_config.json
    - 开发模式：core 父目录的 gui_config.json（兼容旧逻辑）
    """
    if _is_frozen():
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'WxGuiNotifier', 'gui_config.json'
        )
    # 开发模式：项目根目录的 gui_config.json
    return os.path.join(_get_base_dir(), 'gui_config.json')


def find_keys_file() -> str:
    """查找 all_keys.json 文件

    查找优先级：
    1. gui_config.json 中配置的 keys_file（绝对路径，若有效）
    2. exe 所在目录 / 项目根目录的 all_keys.json
    3. PyInstaller 打包到临时目录的 all_keys.json（打包时的静态快照，兜底）
    """
    # 1. 从 gui_config.json 读取 keys_file
    config_file = _get_config_file()
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg_keys = cfg.get('keys_file', '')
            if cfg_keys:
                if not os.path.isabs(cfg_keys):
                    cfg_keys = os.path.join(_get_base_dir(), cfg_keys)
                if os.path.exists(cfg_keys):
                    return cfg_keys
        except Exception as e:
            logger.debug(f"读取 gui_config.json 的 keys_file 失败：{e}")

    # 2. exe 所在目录 / 项目根目录
    base_dir = _get_base_dir()
    candidate = os.path.join(base_dir, 'all_keys.json')
    if os.path.exists(candidate):
        return candidate

    # 3. PyInstaller 临时目录（打包进去的快照）
    if _is_frozen():
        tmp_candidate = os.path.join(sys._MEIPASS, 'all_keys.json')
        if os.path.exists(tmp_candidate):
            return tmp_candidate

    # 4. 开发模式：core 目录、父目录
    if not _is_frozen():
        dev_candidates = [
            os.path.join(os.path.dirname(__file__), 'all_keys.json'),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'all_keys.json'),
        ]
        for c in dev_candidates:
            if os.path.exists(c):
                return c

    # 都没找到，返回默认路径（exe 所在目录），方便后续报错定位
    return candidate


keys_file = find_keys_file()
logger.debug(f"keys_file 解析为：{keys_file}")


# ----------------------------------------------------------------------
# 密钥加载
# ----------------------------------------------------------------------
def load_keys() -> Dict:
    """加载已提取的密钥"""
    if os.path.exists(keys_file):
        try:
            with open(keys_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except json.JSONDecodeError:
            logger.error(f"密钥文件格式错误：{keys_file}")
            return {}
        except Exception as e:
            logger.error(f"读取密钥文件失败：{e}")
            return {}
    return {}


# 全局密钥
ALL_KEYS = load_keys()
HAS_DECRYPT = len(ALL_KEYS) > 0


def reload_keys():
    """重新加载密钥（路径变更后调用）"""
    global ALL_KEYS, HAS_DECRYPT, keys_file
    keys_file = find_keys_file()
    ALL_KEYS = load_keys()
    HAS_DECRYPT = len(ALL_KEYS) > 0
    logger.info(f"密钥重新加载：{len(ALL_KEYS)} 个，路径：{keys_file}")
    return HAS_DECRYPT


# ----------------------------------------------------------------------
# 读取 GUI 配置（统一入口）
# ----------------------------------------------------------------------
def _read_gui_config() -> Dict:
    """读取 GUI 配置文件，返回字典（文件不存在则返回空字典）"""
    config_file = _get_config_file()
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取配置文件失败：{config_file} - {e}")
    return {}


def get_wx_info() -> List[Dict]:
    """
    获取微信信息（支持 4.x 版本）

    :return: 微信信息列表
    """
    # 确保密钥已加载（exe 模式下模块导入时可能尚未就绪，这里兜底重载）
    if not HAS_DECRYPT:
        reload_keys()
    if not HAS_DECRYPT:
        logger.error(f"密钥未加载，无法获取微信信息。keys_file={keys_file}")
        return []

    result = []

    gui_config = _read_gui_config()
    db_dir = gui_config.get('db_dir', '')

    if not db_dir:
        logger.error("配置文件中未找到 db_dir（微信数据库路径）")
        return result

    if not os.path.exists(db_dir):
        logger.error(f"微信数据库目录不存在：{db_dir}")
        return result

    # 从路径中提取 wxid (格式：wxid_xxx_b4c5 或 gally16_b6ea)
    wxid = os.path.basename(os.path.dirname(db_dir))

    # 获取 message_0.db 的密钥（用于监听消息）
    message_key = ALL_KEYS.get('message\\message_0.db', {}).get('enc_key', '')

    # 使用配置中的具体数据库文件路径
    msg_db_path = gui_config.get('msg_db_path', os.path.join(db_dir, 'message', 'message_0.db'))
    micro_db_path = gui_config.get('micro_db_path', os.path.join(db_dir, 'contact', 'contact.db'))

    if message_key:
        info = {
            'pid': 0,  # 不需要 PID
            'version': '4.x (standalone)',
            'wxid': wxid,
            'key': message_key,
            'wx_dir': os.path.dirname(db_dir),
            'msg_path': msg_db_path,
            'micro_path': micro_db_path,
        }
        result.append(info)
        logger.info(f"已加载微信配置：wxid={wxid}")
        logger.info(f"数据库路径：{msg_db_path}")
        logger.info(f"消息密钥：{message_key[:20]}...")
    else:
        logger.error("未找到 message\\message_0.db 的密钥，请确认 all_keys.json 与当前微信账号匹配")

    return result


# ----------------------------------------------------------------------
# 解密函数
# ----------------------------------------------------------------------
def decrypt(key: str, src_path: str, dest_path: str) -> bool:
    """
    解密微信数据库文件

    :param key: 解密密钥
    :param src_path: 源文件路径（数据库文件）
    :param dest_path: 目标文件路径（解密后的文件）
    :return: 是否成功
    """
    try:
        if os.path.isdir(src_path):
            logger.debug(f"跳过目录：{src_path}")
            return False

        if os.path.isdir(dest_path) or not dest_path.endswith('.db'):
            dest_path = os.path.join(dest_path, 'decrypted.db')

        from .wechat_decrypt_core import decrypt_page

        gui_config = _read_gui_config()
        db_dir = gui_config.get('db_dir', '')

        rel_path = os.path.relpath(src_path, db_dir).replace('/', '\\')

        db_key_info = ALL_KEYS.get(rel_path, {})
        enc_key = db_key_info.get('enc_key', key)

        if not enc_key:
            logger.error(f"未找到数据库密钥：{rel_path}")
            return False

        key_bytes = bytes.fromhex(enc_key)

        with open(src_path, 'rb') as f:
            data = f.read()

        page_count = len(data) // PAGE_SZ
        if len(data) % PAGE_SZ != 0:
            page_count += 1

        decrypted_data = bytearray()
        for i in range(page_count):
            start = i * PAGE_SZ
            end = min((i + 1) * PAGE_SZ, len(data))
            page_data = data[start:end]

            if len(page_data) < PAGE_SZ:
                page_data = page_data + b'\x00' * (PAGE_SZ - len(page_data))

            decrypted_page = decrypt_page(key_bytes, page_data, i + 1)
            decrypted_data.extend(decrypted_page)

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        with open(dest_path, 'wb') as f:
            f.write(bytes(decrypted_data))

        logger.debug(f"数据库解密成功：{dest_path} (共{page_count}页)")
        return True
    except Exception as e:
        logger.error(f"解密失败：{e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ----------------------------------------------------------------------
# 微信进程检测（支持 Weixin.exe / WeChat.exe）
# ----------------------------------------------------------------------
def _check_wechat_process() -> bool:
    """检查微信进程是否在运行（兼容 4.x 的 Weixin.exe 和旧版 WeChat.exe）"""
    if sys.platform != 'win32':
        return True  # 非 Windows 环境直接放行

    process_names = ['Weixin.exe', 'WeChat.exe']
    for name in process_names:
        try:
            # tasklist 输出编码可能是 gbk
            output = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /NH',
                shell=True,
                stderr=subprocess.STDOUT
            )
            # 按字节匹配，避免编码问题
            if name.encode('ascii') in output:
                logger.info(f"检测到微信进程：{name}")
                return True
        except Exception as e:
            logger.warning(f"检查进程 {name} 失败：{e}")

    # tasklist 不可用时，尝试 psutil
    try:
        import psutil
        for proc in psutil.process_iter(['name']):
            try:
                pname = proc.info.get('name', '') or ''
                if pname.lower() in ('weixin.exe', 'wechat.exe'):
                    logger.info(f"检测到微信进程（psutil）：{pname}")
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    logger.error("未检测到微信进程在运行（Weixin.exe / WeChat.exe）")
    return False


# ----------------------------------------------------------------------
# 环境初始化（exe 模式兼容）
# ----------------------------------------------------------------------
def init_wechat_env() -> bool:
    """
    初始化微信环境

    检查项：
    1. 密钥文件存在且有效
    2. 微信进程在运行

    exe 模式下不再尝试调用提取脚本（exe 内无源码脚本），
    而是给出明确的错误提示。
    """
    logger.info("=" * 50)
    logger.info("开始初始化微信环境")
    logger.info(f"运行模式：{'exe (frozen)' if _is_frozen() else '开发模式'}")
    logger.info(f"基础目录：{_get_base_dir()}")
    logger.info(f"配置文件：{_get_config_file()}")
    logger.info(f"密钥文件：{keys_file}")

    # 1. 检查密钥文件
    if not os.path.exists(keys_file):
        logger.error(f"密钥文件不存在：{keys_file}")
        if _is_frozen():
            logger.error("【解决方法】请将 all_keys.json 放到 exe 同目录")
            logger.error("            或在程序「切换用户」时重新提取密钥")
        else:
            logger.error("【解决方法】请运行 python utils/auto_extract_keys.py 提取密钥")
        return False

    # 重新加载密钥（确保用最新文件）
    if not reload_keys():
        logger.error("密钥文件为空或格式错误")
        return False

    logger.info(f"密钥加载成功：{len(ALL_KEYS)} 个")

    # 2. 检查配置文件中的 db_dir
    gui_config = _read_gui_config()
    db_dir = gui_config.get('db_dir', '')
    if not db_dir:
        logger.error("配置文件中未设置 db_dir（微信数据库路径）")
        logger.error("【解决方法】请在程序中点击「切换用户」重新选择微信账号")
        return False
    if not os.path.exists(db_dir):
        logger.error(f"微信数据库目录不存在：{db_dir}")
        logger.error("【解决方法】请确认微信数据目录未被移动，或重新选择账号")
        return False
    logger.info(f"微信数据库目录：{db_dir}")

    # 3. 检查关键密钥是否存在
    message_key = ALL_KEYS.get('message\\message_0.db', {}).get('enc_key', '')
    session_key = ALL_KEYS.get('session\\session.db', {}).get('enc_key', '')
    if not message_key:
        logger.error("all_keys.json 中缺少 message\\message_0.db 的密钥")
        logger.error("【解决方法】密钥可能与当前微信账号不匹配，请重新提取")
        return False
    if not session_key:
        logger.warning("all_keys.json 中缺少 session\\session.db 的密钥，会话监听可能失败")
    logger.info("关键密钥检查通过")

    # 4. 检查微信进程
    if not _check_wechat_process():
        logger.error("【解决方法】请启动微信 4.0+ 并登录账号后重试")
        return False

    logger.info("微信环境初始化成功")
    logger.info("=" * 50)
    return True


# ----------------------------------------------------------------------
# GUI 配置读写（统一路径，与 utils.gui_config 一致）
# ----------------------------------------------------------------------
def set_gui_config(db_dir: str, wxid: str, keys_file_path: str = None):
    """
    设置 GUI 配置

    :param db_dir: 数据库目录
    :param wxid: 微信 ID
    :param keys_file_path: 密钥文件路径（可选）
    """
    config_file = _get_config_file()

    # 读取现有配置，合并更新（避免覆盖其它字段）
    config = _read_gui_config()
    config['db_dir'] = db_dir
    config['wxid'] = wxid
    if keys_file_path:
        config['keys_file'] = keys_file_path
    else:
        config['keys_file'] = os.path.join(_get_base_dir(), 'all_keys.json')

    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    logger.info(f"GUI 配置已保存：{config_file}")


def get_gui_config() -> Dict:
    """获取 GUI 配置"""
    return _read_gui_config()


# ----------------------------------------------------------------------
# 测试入口
# ----------------------------------------------------------------------
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    print("测试微信信息获取...")
    print(f"基础目录：{_get_base_dir()}")
    print(f"配置文件：{_get_config_file()}")
    print(f"密钥文件：{keys_file}")
    print(f"密钥数量：{len(ALL_KEYS)}")
    print()
    info_list = get_wx_info()
    if info_list:
        print(f"\n找到 {len(info_list)} 个微信实例")
        for info in info_list:
            print(f"  - wxid: {info['wxid']}")
            print(f"  - 版本：{info['version']}")
            print(f"  - 密钥：{info['key'][:20]}...")
            print(f"  - 路径：{info['msg_path']}")
    else:
        print("未找到微信实例")
