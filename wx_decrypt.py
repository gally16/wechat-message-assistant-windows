"""
微信数据库解密模块
基于 wechat-decrypt 项目 (https://github.com/ylytdeng/wechat-decrypt)
支持微信 4.x 版本
"""

import os
import sys
import json
from typing import List, Dict

# 常量定义
PAGE_SZ = 4096

# wechat-decrypt 路径
wechat_decrypt_path = os.path.join(os.path.dirname(__file__), 'wechat_decrypt_temp')
keys_file = os.path.join(wechat_decrypt_path, 'all_keys.json')
config_file = os.path.join(wechat_decrypt_path, 'config.json')

# 加载密钥
def load_keys():
    """加载已提取的密钥"""
    if os.path.exists(keys_file):
        with open(keys_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# 加载配置
def load_wx_config():
    """加载微信配置"""
    if os.path.exists(config_file):
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

ALL_KEYS = load_keys()
WX_CONFIG = load_wx_config()
HAS_DECRYPT = len(ALL_KEYS) > 0


def get_wx_info() -> List[Dict]:
    """
    获取微信信息（支持 4.x 版本）
    :return: 微信信息列表
    """
    if not HAS_DECRYPT:
        return []
    
    result = []
    
    # 从配置中获取 wxid 和路径
    db_dir = WX_CONFIG.get('db_dir', '')
    
    if not db_dir:
        print("未找到微信数据库路径")
        return result
    
    # 从路径中提取 wxid (格式：wxid_xxx_b4c5)
    wxid = os.path.basename(os.path.dirname(db_dir))
    
    # 获取 message_0.db 的密钥（用于监听消息）
    message_key = ALL_KEYS.get('message\\message_0.db', {}).get('enc_key', '')
    
    # 使用配置中的具体数据库文件路径
    msg_db_path = WX_CONFIG.get('msg_db_path', os.path.join(db_dir, 'message\\message_0.db'))
    micro_db_path = WX_CONFIG.get('micro_db_path', os.path.join(db_dir, 'contact\\contact.db'))
    
    if message_key:
        info = {
            'pid': 0,  # 不需要 PID
            'version': '4.x (wechat-decrypt)',
            'wxid': wxid,
            'key': message_key,
            'wx_dir': os.path.dirname(db_dir),
            'msg_path': msg_db_path,
            'micro_path': micro_db_path,
        }
        result.append(info)
        print(f"已加载微信配置：wxid={wxid}")
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
        
        sys.path.insert(0, wechat_decrypt_path)
        from decrypt_db import decrypt_page
        import json
        
        # 读取所有密钥
        with open(keys_file, 'r', encoding='utf-8') as f:
            all_keys = json.load(f)
        
        # 获取相对路径
        db_dir = WX_CONFIG.get('db_dir', '')
        rel_path = os.path.relpath(src_path, db_dir).replace('/', '\\')
        
        # 获取该数据库的密钥
        db_key_info = all_keys.get(rel_path, {})
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


# 测试函数
if __name__ == '__main__':
    print("测试微信信息获取...")
    info_list = get_wx_info()
    if info_list:
        print(f"\n找到 {len(info_list)} 个微信实例:")
        for info in info_list:
            print(f"  - wxid: {info['wxid']}")
            print(f"  - 版本：{info['version']}")
            print(f"  - 密钥：{info['key'][:20]}...")
            print(f"  - 路径：{info['msg_path']}")
    else:
        print("未找到微信实例")
