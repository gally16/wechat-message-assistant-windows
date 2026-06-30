"""
自动密钥提取工具

直接从微信进程内存中提取密钥并保存到 all_keys.json
"""
import os
import sys
import json

def extract_keys():
    """提取微信数据库密钥"""
    print("=" * 70)
    print("微信数据库密钥提取工具")
    print("=" * 70)
    print()
    
    # 导入配置
    from .gui_config import get_gui_config, CONFIG_FILE
    import json
    import os
    
    # 读取配置文件
    if not os.path.exists(CONFIG_FILE):
        print("❌ 配置文件不存在")
        return False
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"❌ 读取配置文件失败：{e}")
        return False
    
    db_dir = config.get("db_dir")
    keys_file = config.get("keys_file")
    
    if not db_dir:
        print("❌ 未配置微信数据库目录")
        print("请先在 gui_config.json 中配置 db_dir 字段")
        return False
    
    if not keys_file:
        print("❌ 未配置密钥文件路径")
        return False
    
    print(f"✓ 数据库目录：{db_dir}")
    print(f"✓ 密钥文件：{keys_file}")
    print()
    
    # 检查数据库目录是否存在
    if not os.path.exists(db_dir):
        print(f"❌ 数据库目录不存在：{db_dir}")
        print("请确保微信已登录并创建了数据库文件")
        return False
    
    # 检查微信是否运行
    import subprocess
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True)
    if "Weixin.exe" not in result.stdout:
        print("❌ 微信未运行")
        print("请先登录微信，然后再运行此工具")
        return False
    
    print("✓ 检测到微信正在运行")
    print()
    
    # 运行密钥提取
    print("正在从微信进程内存中提取密钥...")
    print("-" * 70)
    
    try:
        from .key_extractor import extract_keys_windows
        success = extract_keys_windows(db_dir, keys_file)
        
        if success:
            print()
            print("=" * 70)
            print("✓ 密钥提取成功！")
            print(f"✓ 密钥文件：{keys_file}")
            print("=" * 70)
            return True
        else:
            print()
            print("❌ 密钥提取失败")
            return False
            
    except ImportError as e:
        print(f"❌ 导入密钥提取模块失败：{e}")
        return False
    except Exception as e:
        print(f"❌ 密钥提取失败：{e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = extract_keys()
    sys.exit(0 if success else 1)
