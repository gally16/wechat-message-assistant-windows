"""
WxGuiNotifier 编译脚本
使用 PyInstaller 将 wx_gui_notifier.py 编译为 exe
"""

import os
import sys
import subprocess
import shutil

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 输出目录
DIST_DIR = os.path.join(PROJECT_ROOT, 'dist')
# 构建目录
BUILD_DIR = os.path.join(PROJECT_ROOT, 'build')
# 图标路径
ICON_PATH = os.path.join(PROJECT_ROOT, 'src', 'img', 'WeChat.ico')
# 主程序
MAIN_SCRIPT = os.path.join(PROJECT_ROOT, 'wx_gui_notifier.py')
# 应用名称
APP_NAME = 'WxGuiNotifier'

def clean():
    """清理旧的构建文件"""
    print("=" * 60)
    print("清理旧的构建文件...")
    print("=" * 60)
    
    # 删除 dist 目录
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
        print(f"✓ 删除 {DIST_DIR}")
    
    # 删除 build 目录
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
        print(f"✓ 删除 {BUILD_DIR}")
    
    # 删除 .spec 文件
    spec_file = os.path.join(PROJECT_ROOT, f'{APP_NAME}.spec')
    if os.path.exists(spec_file):
        os.remove(spec_file)
        print(f"✓ 删除 {spec_file}")
    
    print()

def check_dependencies():
    """检查是否安装了必要的依赖"""
    print("=" * 60)
    print("检查依赖...")
    print("=" * 60)
    
    try:
        import PyInstaller
        print(f"✓ PyInstaller {PyInstaller.__version__} 已安装")
    except ImportError:
        print("✗ PyInstaller 未安装，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✓ PyInstaller 安装完成")
    
    print()

def build():
    """执行编译"""
    print("=" * 60)
    print(f"开始编译 {APP_NAME}...")
    print("=" * 60)
    
    # 确保图标文件存在
    if not os.path.exists(ICON_PATH):
        print(f"✗ 图标文件不存在：{ICON_PATH}")
        sys.exit(1)
    print(f"✓ 图标文件：{ICON_PATH}")
    
    # 构建 PyInstaller 命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",  # 不显示控制台
        "--onefile",   # 单个文件
        "--icon", ICON_PATH,
        "--add-data", f"wechat-decrypt{os.pathsep}wechat-decrypt",  # 包含 wechat-decrypt 目录
        "--add-data", f"src{os.pathsep}src",  # 包含 src 目录（图标等）
        "--hidden-import", "PyQt5",
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "qfluentwidgets",
        "--hidden-import", "Crypto.Cipher.AES",
        "--hidden-import", "sqlite3",
        "--hidden-import", "json",
        "--hidden-import", "threading",
        "--hidden-import", "datetime",
        "--hidden-import", "collections",
        "--hidden-import", "ctypes",
        "--hidden-import", "struct",
        "--hidden-import", "watchdog",
        "--hidden-import", "winotify",
        "--noconfirm",  # 覆盖输出目录
        MAIN_SCRIPT
    ]
    
    print(f"执行命令：{' '.join(cmd)}")
    print()
    
    # 执行编译
    try:
        subprocess.check_call(cmd)
        print()
        print("=" * 60)
        print("✓ 编译成功！")
        print("=" * 60)
        
        # 显示输出文件位置
        exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
        print(f"\n可执行文件位置：{exe_path}")
        print(f"文件大小：{os.path.getsize(exe_path) / 1024 / 1024:.1f} MB")
        
    except subprocess.CalledProcessError as e:
        print()
        print("=" * 60)
        print("✗ 编译失败！")
        print("=" * 60)
        print(f"错误：{e}")
        sys.exit(1)

def main():
    """主函数"""
    print()
    print("*" * 60)
    print(f"*  {APP_NAME} 编译脚本".center(58) + " *")
    print("*" * 60)
    print()
    
    # 切换到项目根目录
    os.chdir(PROJECT_ROOT)
    
    # 1. 清理
    clean()
    
    # 2. 检查依赖
    check_dependencies()
    
    # 3. 编译
    build()
    
    print()
    print("=" * 60)
    print("完成！")
    print("=" * 60)
    print()

if __name__ == "__main__":
    main()
