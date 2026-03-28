"""
微信数据库解密模块（完全独立版）

不依赖 wechat-decrypt 目录，所有功能都集成在此文件中
支持微信 4.x 版本
"""

import os
import sys
import json
from typing import List, Dict

# 常量定义
PAGE_SZ = 4096

# 密钥文件路径（指向项目根目录的 all_keys.json）
# 尝试多个可能的位置
def find_keys_file():
    """查找 all_keys.json 文件"""
    possible_paths = [
        # 优先使用项目根目录（当作为包使用时）
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'all_keys.json'),  # 项目根目录
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'all_keys.json'),  # 父目录
        os.path.join(os.path.dirname(__file__), 'all_keys.json'),  # 当前目录
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # 如果都不存在，返回默认路径（项目根目录）
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'all_keys.json')

keys_file = find_keys_file()

# 加载密钥
def load_keys():
    """加载已提取的密钥"""
    if os.path.exists(keys_file):
        try:
            with open(keys_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    # 文件为空
                    return {}
                return json.loads(content)
        except json.JSONDecodeError:
            # JSON 格式错误
            print(f"[!] 密钥文件格式错误：{keys_file}")
            return {}
    return {}

# 全局密钥
ALL_KEYS = load_keys()
HAS_DECRYPT = len(ALL_KEYS) > 0


def get_wx_info() -> List[Dict]:
    """
    获取微信信息（支持 4.x 版本）
    
    :return: 微信信息列表
    """
    if not HAS_DECRYPT:
        return []

    result = []

    # 从 gui_config.json 获取配置
    gui_config_file = os.path.join(os.path.dirname(__file__), 'gui_config.json')
    if os.path.exists(gui_config_file):
        with open(gui_config_file, 'r', encoding='utf-8') as f:
            gui_config = json.load(f)
    else:
        gui_config = {}

    db_dir = gui_config.get('db_dir', '')

    if not db_dir:
        print("未找到微信数据库路径")
        return result

    # 从路径中提取 wxid (格式：wxid_xxx_b4c5)
    wxid = os.path.basename(os.path.dirname(db_dir))

    # 获取 message_0.db 的密钥（用于监听消息）
    message_key = ALL_KEYS.get('message\\message_0.db', {}).get('enc_key', '')

    # 使用配置中的具体数据库文件路径
    msg_db_path = gui_config.get('msg_db_path', os.path.join(db_dir, 'message\\message_0.db'))
    micro_db_path = gui_config.get('micro_db_path', os.path.join(db_dir, 'contact\\contact.db'))

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
        print(f"[+] 已加载微信配置：wxid={wxid}")
        print(f"数据库路径：{msg_db_path}")
        print(f"消息密钥：{message_key[:20]}...")
    else:
        print("未找到消息数据库密钥")

    return result


def decrypt(key: str, src_path: str, dest_path: str) -> bool:
    """
    解密微信数据库文件
    
    :param key: 解密密钥
    :param src_path: 源文件路径（数据库文件）
    :param dest_path: 目标文件路径（解密后的文件）
    :return: 是否成功
    """
    try:
        # 如果 src_path 是目录，不解密
        if os.path.isdir(src_path):
            print(f"跳过目录：{src_path}")
            return False

        # 如果目标路径是目录，创建具体文件名
        if os.path.isdir(dest_path) or not dest_path.endswith('.db'):
            dest_path = os.path.join(dest_path, 'decrypted.db')

        # 导入本地解密函数
        from .wechat_decrypt_core import decrypt_page

        # 获取相对路径
        gui_config_file = os.path.join(os.path.dirname(__file__), 'gui_config.json')
        if os.path.exists(gui_config_file):
            with open(gui_config_file, 'r', encoding='utf-8') as f:
                gui_config = json.load(f)
            db_dir = gui_config.get('db_dir', '')
        else:
            db_dir = ''

        rel_path = os.path.relpath(src_path, db_dir).replace('/', '\\')

        # 获取该数据库的密钥
        db_key_info = ALL_KEYS.get(rel_path, {})
        enc_key = db_key_info.get('enc_key', key)

        if not enc_key:
            print(f"未找到数据库密钥：{rel_path}")
            return False

        # 将 hex 字符串转换为字节
        key_bytes = bytes.fromhex(enc_key)

        # 读取源文件
        with open(src_path, 'rb') as f:
            data = f.read()

        # 计算页面数量
        page_count = len(data) // PAGE_SZ
        if len(data) % PAGE_SZ != 0:
            page_count += 1

        # 解密所有页面
        decrypted_data = bytearray()
        for i in range(page_count):
            start = i * PAGE_SZ
            end = min((i + 1) * PAGE_SZ, len(data))
            page_data = data[start:end]

            # 填充到完整页面
            if len(page_data) < PAGE_SZ:
                page_data = page_data + b'\x00' * (PAGE_SZ - len(page_data))

            # 解密页面
            decrypted_page = decrypt_page(key_bytes, page_data, i + 1)
            decrypted_data.extend(decrypted_page)

        # 创建目标目录
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # 写入解密后的文件
        with open(dest_path, 'wb') as f:
            f.write(bytes(decrypted_data))

        print(f"数据库解密成功：{dest_path} (共{page_count}页)")
        return True
    except Exception as e:
        print(f"解密失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def init_wechat_env():
    """
    初始化微信环境（兼容旧版本）
    
    这个函数主要用于设置环境变量和检查微信进程
    如果密钥文件不存在，会自动尝试提取密钥
    """
    import ctypes
    import subprocess
    
    # 检查密钥文件是否存在，不存在则尝试提取
    if not os.path.exists(keys_file):
        print("[!] 密钥文件不存在，尝试自动提取...")
        print(f"    目标路径：{keys_file}")
        
        # 尝试在项目中查找密钥提取脚本
        possible_scripts = [
            os.path.join(os.path.dirname(__file__), 'auto_extract_keys.py'),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'auto_extract_keys.py'),
        ]
        
        script_found = False
        for script in possible_scripts:
            if os.path.exists(script):
                print(f"    找到密钥提取脚本：{script}")
                try:
                    # 运行密钥提取脚本
                    result = subprocess.run(
                        ['python', script],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    print(result.stdout)
                    if result.returncode == 0:
                        print("[+] 密钥提取成功！")
                        script_found = True
                        break
                except Exception as e:
                    print(f"[-] 密钥提取失败：{e}")
        
        if not script_found:
            print("[-] 未找到密钥提取脚本")
            print("[!] 请手动运行密钥提取工具")
            print("    运行命令：python auto_extract_keys.py")
            return False
    
    # 重新加载密钥
    global ALL_KEYS, HAS_DECRYPT
    ALL_KEYS = load_keys()
    HAS_DECRYPT = len(ALL_KEYS) > 0
    
    if not HAS_DECRYPT:
        print("[-] 密钥文件为空或格式错误")
        return False
    
    # 检查微信进程
    try:
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq Weixin.exe" /NH',
            shell=True,
            stderr=subprocess.STDOUT
        )
        if b'Weixin.exe' in output:
            print("[+] 微信进程运行中")
            return True
        else:
            print("[-] 未检测到已登录的微信进程")
            return False
    except Exception as e:
        print(f"检查微信进程失败：{e}")
        return False


def set_gui_config(db_dir: str, wxid: str, keys_file_path: str = None):
    """
    设置 GUI 配置（兼容旧版本）
    
    :param db_dir: 数据库目录
    :param wxid: 微信 ID
    :param keys_file_path: 密钥文件路径（可选）
    """
    gui_config_file = os.path.join(os.path.dirname(__file__), 'gui_config.json')
    
    config = {
        'db_dir': db_dir,
        'wxid': wxid,
        'keys_file': keys_file_path or os.path.join(os.path.dirname(__file__), 'all_keys.json'),
    }
    
    with open(gui_config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    print(f"GUI 配置已保存：{gui_config_file}")


def get_gui_config():
    """
    获取 GUI 配置
    
    :return: GUI 配置字典
    """
    gui_config_file = os.path.join(os.path.dirname(__file__), 'gui_config.json')
    if os.path.exists(gui_config_file):
        with open(gui_config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


# 测试函数
if __name__ == '__main__':
    print("测试微信信息获取...")
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
