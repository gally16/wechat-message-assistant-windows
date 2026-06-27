"""
WxGuiNotifier 完整打包脚本

功能：
1. 清理旧的构建文件
2. 检查并安装依赖
3. 验证必要的文件
4. 执行 PyInstaller 打包
5. 创建安装包（可选）
6. 生成校验和

使用方法：
    python build.py [--clean] [--install] [--verify]
    
参数：
    --clean   : 强制清理旧构建
    --install : 重新安装依赖
    --verify  : 验证打包结果
"""

import os
import sys
import subprocess
import shutil
import hashlib
import json
from pathlib import Path
from datetime import datetime

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()
# 输出目录
DIST_DIR = PROJECT_ROOT / 'dist'
# 构建目录
BUILD_DIR = PROJECT_ROOT / 'build'
# 图标路径
ICON_PATH = PROJECT_ROOT / 'src' / 'img' / 'WeChat.ico'
# 主程序
MAIN_SCRIPT = PROJECT_ROOT / 'wx_gui_notifier.py'
# 应用名称
APP_NAME = 'WxGuiNotifier'
# 版本文件
VERSION_FILE = PROJECT_ROOT / 'version.json'


def print_header(text):
    """打印标题"""
    print()
    print("=" * 70)
    print(f" {text}".center(69) + "=")
    print("=" * 70)
    print()


def print_step(text):
    """打印步骤"""
    try:
        print(f"📌 {text}")
    except UnicodeEncodeError:
        print(f"[STEP] {text}")


def print_success(text):
    """打印成功信息"""
    print(f"✅ {text}")


def print_error(text):
    """打印错误信息"""
    print(f"❌ {text}")


def print_warning(text):
    """打印警告信息"""
    print(f"⚠️  {text}")


def clean():
    """清理旧的构建文件"""
    print_header("清理旧的构建文件")
    
    # 删除 dist 目录
    if DIST_DIR.exists():
        print_step(f"删除 {DIST_DIR}")
        try:
            shutil.rmtree(DIST_DIR)
            print_success(f"已删除 {DIST_DIR}")
        except Exception as e:
            print_warning(f"删除 {DIST_DIR} 失败：{e}")
    
    # 删除 build 目录
    if BUILD_DIR.exists():
        print_step(f"删除 {BUILD_DIR}")
        try:
            shutil.rmtree(BUILD_DIR)
            print_success(f"已删除 {BUILD_DIR}")
        except Exception as e:
            print_warning(f"删除 {BUILD_DIR} 失败：{e}")
    
    # 删除 .spec 文件
    spec_file = PROJECT_ROOT / f'{APP_NAME}.spec'
    if spec_file.exists():
        print_step(f"删除 {spec_file}")
        try:
            spec_file.unlink()
            print_success(f"已删除 {spec_file}")
        except Exception as e:
            print_warning(f"删除 {spec_file} 失败：{e}")
    
    # 删除 __pycache__ 目录
    pycache_dirs = list(PROJECT_ROOT.rglob('__pycache__'))
    if pycache_dirs:
        print_step(f"删除 {len(pycache_dirs)} 个 __pycache__ 目录")
        for pycache in pycache_dirs:
            try:
                shutil.rmtree(pycache)
            except:
                pass
        print_success(f"已清理 {len(pycache_dirs)} 个 __pycache__ 目录")
    
    print()


def check_dependencies(force_install=False):
    """检查并安装依赖"""
    print_header("检查并安装依赖")
    
    # 读取 requirements.txt
    requirements_file = PROJECT_ROOT / 'requirements.txt'
    if not requirements_file.exists():
        print_error(f"requirements.txt 不存在：{requirements_file}")
        sys.exit(1)
    
    print_step("升级 pip")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    
    print_step("安装/更新依赖")
    try:
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)]
        if force_install:
            cmd.append("--force-reinstall")
        subprocess.check_call(cmd)
        print_success("依赖安装完成")
    except subprocess.CalledProcessError as e:
        print_error(f"依赖安装失败：{e}")
        sys.exit(1)
    
    # 验证 PyInstaller
    try:
        import PyInstaller
        print_success(f"PyInstaller {PyInstaller.__version__} 已安装")
    except ImportError:
        print_error("PyInstaller 未安装")
        sys.exit(1)
    
    print()


def verify_files():
    """验证必要的文件是否存在"""
    print_header("验证必要的文件")
    
    files_to_check = [
        MAIN_SCRIPT,
        ICON_PATH,
        PROJECT_ROOT / 'src',
        PROJECT_ROOT / 'gui_config.example.json',
        PROJECT_ROOT / 'version.json',
        PROJECT_ROOT / 'core',
        PROJECT_ROOT / 'utils',
        PROJECT_ROOT / 'utils' / 'avatar_cache.py',  # 新增头像模块
        PROJECT_ROOT / 'ui',
    ]
    
    all_exist = True
    for file_path in files_to_check:
        if file_path.exists():
            print_success(f"✓ {file_path.name}")
        else:
            print_error(f"✗ {file_path.name} 不存在")
            all_exist = False
    
    if not all_exist:
        print_error("必要的文件缺失，无法继续打包")
        sys.exit(1)
    
    print()


def get_version():
    """获取版本号"""
    if VERSION_FILE.exists():
        with open(VERSION_FILE, 'r', encoding='utf-8') as f:
            version_data = json.load(f)
            return version_data.get('version', '1.0.0')
    return '1.0.0'


def build():
    """执行打包"""
    print_header(f"开始打包 {APP_NAME} v{get_version()}")
    
    # 确保图标文件存在
    if not ICON_PATH.exists():
        print_error(f"图标文件不存在：{ICON_PATH}")
        sys.exit(1)
    print_success(f"图标文件：{ICON_PATH.name}")
    
    # 构建 PyInstaller 命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",  # 不显示控制台
        "--onefile",   # 单个文件
        "--icon", str(ICON_PATH),
        "--add-data", f"src{os.pathsep}src",
        "--add-data", f"core{os.pathsep}core",
        "--add-data", f"utils{os.pathsep}utils",
        "--add-data", f"ui{os.pathsep}ui",
        "--add-data", f"gui_config.example.json{os.pathsep}.",
        "--add-data", f"version.json{os.pathsep}.",
        # all_keys.json 若存在则一并打包（首次运行可省去提取步骤）
    ]
    # 动态追加 all_keys.json
    all_keys_file = PROJECT_ROOT / 'all_keys.json'
    if all_keys_file.exists():
        cmd += ["--add-data", f"all_keys.json{os.pathsep}."]

    cmd += [
        "--hidden-import", "PyQt5",
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "qfluentwidgets",
        "--collect-all", "qfluentwidgets",
        "--hidden-import", "Crypto.Cipher.AES",
        "--hidden-import", "cryptography",
        "--hidden-import", "sqlite3",
        "--hidden-import", "json",
        "--hidden-import", "threading",
        "--hidden-import", "datetime",
        "--hidden-import", "collections",
        "--hidden-import", "urllib.request",
        "--hidden-import", "urllib.error",
        "--hidden-import", "io",
        "--hidden-import", "core.wechat_decrypt_core",
        "--hidden-import", "core.wx_decrypt",
        "--hidden-import", "utils.gui_config",
        "--hidden-import", "utils.auto_extract_keys",
        "--hidden-import", "utils.avatar_cache",
        "--hidden-import", "utils.key_extractor",
        "--hidden-import", "utils.key_scan_common",
        "--hidden-import", "ui.user_selector",
        "--hidden-import", "watchdog",
        "--hidden-import", "watchdog.observers",
        "--hidden-import", "watchdog.events",
        "--hidden-import", "winotify",
        "--hidden-import", "zstandard",
        "--hidden-import", "xmltodict",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "aiofiles",
        "--hidden-import", "yara",
        "--hidden-import", "psutil",
        "--noconfirm",  # 覆盖输出目录
        "--clean",      # 清理临时文件
        str(MAIN_SCRIPT)
    ]
    
    print_step(f"执行 PyInstaller 打包")
    print(f"命令：{' '.join(cmd)}")
    print()
    
    # 执行打包
    try:
        subprocess.check_call(cmd, cwd=str(PROJECT_ROOT))
        print()
        print_success("打包成功！")
    except subprocess.CalledProcessError as e:
        print()
        print_error("打包失败！")
        print_error(f"错误代码：{e}")
        sys.exit(1)
    
    # 验证输出文件
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        file_size = exe_path.stat().st_size
        print_success(f"可执行文件：{exe_path}")
        print_success(f"文件大小：{file_size / 1024 / 1024:.1f} MB")
    else:
        print_error(f"输出文件不存在：{exe_path}")
        sys.exit(1)
    
    print()


def create_checksum():
    """创建文件校验和"""
    print_header("创建文件校验和")
    
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if not exe_path.exists():
        print_error("可执行文件不存在")
        return
    
    # 计算 SHA256
    sha256_hash = hashlib.sha256()
    with open(exe_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    
    checksum = sha256_hash.hexdigest()
    
    # 保存校验和
    checksum_file = DIST_DIR / f"{APP_NAME}.sha256"
    with open(checksum_file, 'w', encoding='utf-8') as f:
        f.write(f"{checksum}  {APP_NAME}.exe\n")
    
    print_success(f"SHA256: {checksum}")
    print_success(f"校验和文件：{checksum_file}")
    print()


def create_build_info():
    """创建构建信息文件"""
    print_header("创建构建信息")
    
    import PyInstaller
    
    build_info = {
        'app_name': APP_NAME,
        'version': get_version(),
        'build_time': datetime.now().isoformat(),
        'python_version': sys.version,
        'pyinstaller_version': PyInstaller.__version__,
        'platform': sys.platform,
        'architecture': '64-bit' if sys.maxsize > 2**32 else '32-bit',
    }
    
    build_info_file = DIST_DIR / 'build_info.json'
    with open(build_info_file, 'w', encoding='utf-8') as f:
        json.dump(build_info, f, indent=2, ensure_ascii=False)
    
    print_success(f"构建信息：{build_info_file}")
    print()


def show_summary():
    """显示打包总结"""
    print_header("打包完成")
    
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    checksum_file = DIST_DIR / f"{APP_NAME}.sha256"
    build_info_file = DIST_DIR / 'build_info.json'
    
    print("📦 输出文件:")
    print(f"   1. {exe_path}")
    print(f"   2. {checksum_file}")
    print(f"   3. {build_info_file}")
    print()
    
    print("📝 使用说明:")
    print("   1. 将 WxGuiNotifier.exe 复制到目标机器")
    print("   2. 确保目标机器已安装微信并登录")
    print("   3. 双击运行 WxGuiNotifier.exe")
    print("   4. 首次运行需要提取微信密钥")
    print()
    
    print("⚠️  注意事项:")
    print("   - 目标机器需要安装 Visual C++ Redistributable")
    print("   - 需要 Windows 10 或更高版本")
    print("   - 需要 .NET Framework 4.7.2 或更高版本")
    print()
    
    print_success("打包完成！")
    print()


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='WxGuiNotifier 打包脚本')
    parser.add_argument('--clean', action='store_true', help='强制清理旧构建')
    parser.add_argument('--install', action='store_true', help='重新安装依赖')
    parser.add_argument('--verify', action='store_true', help='验证打包结果')
    
    args = parser.parse_args()
    
    # 切换到项目根目录
    os.chdir(PROJECT_ROOT)
    
    # 1. 清理
    if args.clean or not args.verify:
        clean()
    
    # 2. 检查并安装依赖
    if args.install or not args.verify:
        check_dependencies(force_install=args.install)
    
    # 3. 验证文件
    verify_files()
    
    # 4. 打包
    build()
    
    # 5. 创建校验和
    create_checksum()
    
    # 6. 创建构建信息
    create_build_info()
    
    # 7. 显示总结
    show_summary()


if __name__ == "__main__":
    main()
