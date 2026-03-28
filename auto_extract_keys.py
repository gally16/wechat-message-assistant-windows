"""
自动密钥提取工具

从 wechat-decrypt 项目提取密钥并保存到 all_keys.json
"""
import os
import sys
import json
import subprocess

def extract_keys():
    """提取微信数据库密钥"""
    print("=" * 70)
    print("微信数据库密钥提取工具")
    print("=" * 70)
    print()
    
    # 检查 wechat-decrypt 目录
    wechat_decrypt_dir = None
    
    # 尝试查找 wechat-decrypt 目录
    possible_dirs = [
        os.path.join(os.path.dirname(__file__), 'wechat-decrypt'),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'wechat-decrypt'),
    ]
    
    for dir_path in possible_dirs:
        if os.path.exists(dir_path):
            wechat_decrypt_dir = dir_path
            break
    
    if not wechat_decrypt_dir:
        print("❌ 未找到 wechat-decrypt 目录")
        print("请确保 wechat-decrypt 目录存在于项目目录中")
        return False
    
    print(f"✓ 找到 wechat-decrypt 目录：{wechat_decrypt_dir}")
    print()
    
    # 查找密钥提取脚本
    script_path = os.path.join(wechat_decrypt_dir, 'find_all_keys_windows.py')
    
    if not os.path.exists(script_path):
        print("❌ 未找到密钥提取脚本：find_all_keys_windows.py")
        print("请确保 wechat-decrypt 目录中包含此文件")
        return False
    
    print(f"✓ 找到密钥提取脚本：{script_path}")
    print()
    
    # 运行密钥提取
    print("正在运行密钥提取...")
    print("-" * 70)
    
    try:
        result = subprocess.run(
            ['python', script_path],
            cwd=wechat_decrypt_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        print(result.stdout)
        
        if result.returncode != 0:
            print(f"❌ 密钥提取失败：{result.stderr}")
            return False
        
        # 检查是否生成了 all_keys.json
        output_file = os.path.join(wechat_decrypt_dir, 'all_keys.json')
        
        if os.path.exists(output_file):
            print()
            print("=" * 70)
            print("✓ 密钥提取成功！")
            print(f"✓ 密钥文件：{output_file}")
            print("=" * 70)
            
            # 复制到项目根目录
            target_file = os.path.join(os.path.dirname(__file__), 'all_keys.json')
            
            try:
                import shutil
                shutil.copy2(output_file, target_file)
                print(f"✓ 密钥文件已复制到：{target_file}")
                print()
                print("现在可以关闭此窗口并运行主程序")
                return True
            except Exception as e:
                print(f"❌ 复制密钥文件失败：{e}")
                print(f"请手动将 {output_file} 复制到 {target_file}")
                return True
        else:
            print("❌ 密钥提取完成，但未找到 all_keys.json 文件")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ 密钥提取超时")
        return False
    except Exception as e:
        print(f"❌ 密钥提取失败：{e}")
        return False

if __name__ == '__main__':
    success = extract_keys()
    sys.exit(0 if success else 1)
