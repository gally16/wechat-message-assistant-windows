# -*- mode: python ; coding: utf-8 -*-
"""
WxGuiNotifier PyInstaller 打包配置

使用相对路径，可在任意 Windows 机器上直接复用。
打包命令：
    pyinstaller WxGuiNotifier.spec --noconfirm --clean
或：
    python build.py
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 项目根目录（.spec 文件所在目录）
PROJ = os.path.dirname(os.path.abspath(SPEC))

# 收集 qfluentwidgets 的所有子模块与数据文件（图标/QSS 等需要随包分发）
qfluent_datas = collect_data_files('qfluentwidgets')
qfluent_hidden = collect_submodules('qfluentwidgets')

# 收集 PyQt5 数据文件（插件、翻译）
pyqt5_datas = collect_data_files('PyQt5')

# 收集 winotify 数据文件（XML 模板）
winotify_datas = collect_data_files('winotify')

# 额外数据文件：源码目录、配置示例、密钥文件（若存在）
extra_datas = [
    ('src', 'src'),
    ('core', 'core'),
    ('utils', 'utils'),
    ('ui', 'ui'),
    ('gui_config.example.json', '.'),
    ('version.json', '.'),
    ('LICENSE', '.'),
]
# all_keys.json 若存在则一并打包（首次运行可省去提取步骤）
all_keys_path = os.path.join(PROJ, 'all_keys.json')
if os.path.exists(all_keys_path):
    extra_datas.append(('all_keys.json', '.'))

a = Analysis(
    [os.path.join(PROJ, 'wx_gui_notifier.py')],
    pathex=[PROJ],
    binaries=[],
    datas=qfluent_datas + pyqt5_datas + winotify_datas + extra_datas,
    hiddenimports=[
        # PyQt5
        'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.sip',
        # qfluentwidgets（动态收集子模块，避免漏掉内部组件）
        *qfluent_hidden,
        # 加解密
        'Crypto.Cipher.AES', 'Crypto.Cipher.DES', 'Crypto.Util.Padding',
        'cryptography',
        # 标准库（显式声明，避免某些精简环境漏掉）
        'sqlite3', 'json', 'threading', 'datetime', 'collections',
        'urllib.request', 'urllib.error', 'io', 'hashlib', 'struct', 'ctypes',
        # 项目内部模块（新增 utils.avatar_cache 必须显式声明）
        'core.wechat_decrypt_core', 'core.wx_decrypt',
        'utils.gui_config', 'utils.auto_extract_keys',
        'utils.avatar_cache', 'utils.key_extractor', 'utils.key_scan_common',
        'ui.user_selector',
        # 第三方依赖
        'watchdog', 'watchdog.observers', 'watchdog.events',
        'winotify', 'winotify.audio',
        'zstandard', 'xmltodict',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'aiofiles',
        'yara',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WxGuiNotifier',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['PyQt5\\Qt5\\bin\\Qt5Core.dll',
                 'PyQt5\\Qt5\\bin\\Qt5Gui.dll',
                 'PyQt5\\Qt5\\bin\\Qt5Widgets.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(PROJ, 'src', 'img', 'WeChat.ico'),
)
