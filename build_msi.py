"""
WxGuiNotifier MSI 安装包创建脚本

功能：
1. 先使用 PyInstaller 打包 EXE
2. 使用 Inno Setup 创建 MSI 安装包
3. 自动检查并安装依赖（VC++、.NET Framework）

前提条件：
- Python 3.8+
- PyInstaller
- Inno Setup 6.x (https://jrsoftware.org/isdl.php)

使用方法：
    python build_msi.py
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()
# 输出目录
DIST_DIR = PROJECT_ROOT / 'dist'
# 安装包输出目录
INSTALLER_DIR = PROJECT_ROOT / 'installer_output'
# Inno Setup 编译器路径（默认安装位置）
ISCC_PATH = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
# 应用名称
APP_NAME = "WxGuiNotifier"
# 版本
VERSION = "1.0.0"


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
        print(f"[STEP] {text}")
    except:
        print(f"Step: {text}")


def print_success(text):
    """打印成功信息"""
    try:
        print(f"[OK] {text}")
    except:
        print(f"Success: {text}")


def print_error(text):
    """打印错误信息"""
    try:
        print(f"[ERROR] {text}")
    except:
        print(f"Error: {text}")


def print_warning(text):
    """打印警告信息"""
    try:
        print(f"[WARNING] {text}")
    except:
        print(f"Warning: {text}")


def check_prerequisites():
    """检查前提条件"""
    global ISCC_PATH  # 在函数开头声明
    print_header("检查前提条件")
    
    # 检查 PyInstaller
    try:
        import PyInstaller
        print_success(f"PyInstaller {PyInstaller.__version__} 已安装")
    except ImportError:
        print_error("PyInstaller 未安装，请先安装：pip install pyinstaller")
        sys.exit(1)
    
    # 检查 Inno Setup
    if ISCC_PATH.exists():
        print_success(f"Inno Setup 已安装：{ISCC_PATH}")
    else:
        # 尝试其他常见路径
        alt_paths = [
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 5\ISCC.exe"),
        ]
        found = False
        for alt_path in alt_paths:
            if alt_path.exists():
                ISCC_PATH = alt_path
                print_success(f"Inno Setup 已安装：{ISCC_PATH}")
                found = True
                break
        
        if not found:
            print_warning("Inno Setup 未找到")
            print("请下载并安装：https://jrsoftware.org/isdl.php")
            print("或者继续打包 EXE 文件（跳过 MSI 创建）")
            response = input("是否继续打包 EXE？(y/n): ")
            if response.lower() != 'y':
                sys.exit(1)
            return False
    
    return True


def build_exe():
    """打包 EXE"""
    print_header("打包 EXE 文件")
    
    # 清理旧文件
    if DIST_DIR.exists():
        print_step(f"清理旧的 {DIST_DIR.name} 目录")
        shutil.rmtree(DIST_DIR)
    
    # PyInstaller 命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onefile",
        "--icon", str(PROJECT_ROOT / "src" / "img" / "WeChat.ico"),
        "--add-data", f"src{os.pathsep}src",
        "--add-data", f"core{os.pathsep}core",
        "--add-data", f"utils{os.pathsep}utils",
        "--add-data", f"ui{os.pathsep}ui",
        "--hidden-import", "PyQt5",
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "qfluentwidgets",
        "--hidden-import", "Crypto.Cipher.AES",
        "--hidden-import", "cryptography",
        "--hidden-import", "sqlite3",
        "--hidden-import", "json",
        "--hidden-import", "threading",
        "--hidden-import", "datetime",
        "--hidden-import", "collections",
        "--hidden-import", "core.wechat_decrypt_core",
        "--hidden-import", "core.wx_decrypt",
        "--hidden-import", "utils.gui_config",
        "--hidden-import", "utils.auto_extract_keys",
        "--hidden-import", "ui.user_selector",
        "--hidden-import", "watchdog",
        "--hidden-import", "winotify",
        "--hidden-import", "zstandard",
        "--hidden-import", "xmltodict",
        "--hidden-import", "PIL",
        "--hidden-import", "aiofiles",
        "--hidden-import", "yara",
        "--hidden-import", "psutil",
        "--noconfirm",
        "--clean",
        str(PROJECT_ROOT / "wx_gui_notifier.py")
    ]
    
    print_step(f"执行 PyInstaller 打包")
    print(f"命令：{' '.join(cmd)}")
    print()
    
    try:
        subprocess.check_call(cmd, cwd=str(PROJECT_ROOT))
        print_success("EXE 打包成功！")
    except subprocess.CalledProcessError as e:
        print_error(f"EXE 打包失败：{e}")
        sys.exit(1)
    
    # 验证输出
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        file_size = exe_path.stat().st_size
        print_success(f"可执行文件：{exe_path}")
        print_success(f"文件大小：{file_size / 1024 / 1024:.1f} MB")
    else:
        print_error(f"输出文件不存在：{exe_path}")
        sys.exit(1)
    
    print()
    return True


def create_msi():
    """使用 Inno Setup 创建 MSI 安装包"""
    print_header("创建 MSI 安装包")
    
    # 检查 Inno Setup
    if not ISCC_PATH.exists():
        print_error("Inno Setup 未安装，跳过 MSI 创建")
        return False
    
    # 检查 .iss 文件
    iss_file = PROJECT_ROOT / f"{APP_NAME}.iss"
    if not iss_file.exists():
        print_error(f"安装脚本不存在：{iss_file}")
        return False
    
    # 检查 EXE 是否存在
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if not exe_path.exists():
        print_error(f"EXE 文件不存在，请先打包 EXE: {exe_path}")
        return False
    
    # 创建输出目录
    if not INSTALLER_DIR.exists():
        INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
        print_step(f"创建输出目录：{INSTALLER_DIR}")
    
    # 编译安装脚本
    print_step(f"使用 Inno Setup 编译安装包")
    print(f"编译器：{ISCC_PATH}")
    print(f"脚本：{iss_file}")
    print()
    
    cmd = [str(ISCC_PATH), str(iss_file), "/O" + str(INSTALLER_DIR)]
    
    try:
        subprocess.check_call(cmd, cwd=str(PROJECT_ROOT))
        print_success("MSI 安装包创建成功！")
    except subprocess.CalledProcessError as e:
        print_error(f"MSI 创建失败：{e}")
        return False
    
    # 查找输出文件
    msi_files = list(INSTALLER_DIR.glob(f"{APP_NAME}_Setup_*.exe"))
    if msi_files:
        msi_path = msi_files[0]
        file_size = msi_path.stat().st_size
        print_success(f"安装包：{msi_path}")
        print_success(f"文件大小：{file_size / 1024 / 1024:.1f} MB")
    else:
        print_warning("未找到生成的安装包")
    
    print()
    return True


def show_summary():
    """显示打包总结"""
    print_header("打包完成")
    
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    msi_files = list(INSTALLER_DIR.glob(f"{APP_NAME}_Setup_*.exe"))
    
    print("📦 输出文件:")
    if exe_path.exists():
        print(f"   1. {exe_path}")
        print(f"      大小：{exe_path.stat().st_size / 1024 / 1024:.1f} MB")
    
    if msi_files:
        msi_path = msi_files[0]
        print(f"   2. {msi_path}")
        print(f"      大小：{msi_path.stat().st_size / 1024 / 1024:.1f} MB")
    
    print()
    
    print("📝 使用说明:")
    print("   方法 1: 直接运行 EXE")
    print("      - 复制 WxGuiNotifier.exe 到目标机器")
    print("      - 双击运行")
    print()
    print("   方法 2: 使用安装包（推荐）")
    print("      - 运行 WxGuiNotifier_Setup_x.x.x.exe")
    print("      - 按照提示安装")
    print("      - 自动检查并安装 VC++ 运行库")
    print("      - 自动创建开始菜单和桌面快捷方式")
    print()
    
    print("⚠️  注意事项:")
    print("   - 安装包会自动检查 VC++ 运行库")
    print("   - 如果没有安装，会自动下载并安装")
    print("   - 需要 Windows 10 或更高版本")
    print("   - 需要管理员权限安装")
    print()
    
    print_success("打包完成！")
    print()


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='WxGuiNotifier MSI 打包脚本')
    parser.add_argument('--skip-m si', action='store_true', help='跳过 MSI 创建，只打包 EXE')
    parser.add_argument('--only-exe', action='store_true', help='只打包 EXE')
    
    args = parser.parse_args()
    
    # 切换到项目根目录
    os.chdir(PROJECT_ROOT)
    
    # 1. 检查前提条件
    has_inno_setup = check_prerequisites()
    
    # 2. 打包 EXE
    if not build_exe():
        print_error("EXE 打包失败")
        sys.exit(1)
    
    # 3. 创建 MSI
    if not args.only_exe and has_inno_setup:
        if not create_msi():
            print_warning("MSI 创建失败，但 EXE 已生成")
    elif not has_inno_setup:
        print_warning("跳过 MSI 创建（未安装 Inno Setup）")
    
    # 4. 显示总结
    show_summary()


if __name__ == "__main__":
    main()
