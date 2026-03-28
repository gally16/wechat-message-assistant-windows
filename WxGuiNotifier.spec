# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\wangc\\Documents\\code\\python\\WxGuiNotifier\\wx_gui_notifier.py'],
    pathex=[],
    binaries=[],
    datas=[('all_keys.json', '.'), ('src', 'src')],
    hiddenimports=['PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.sip', 'qfluentwidgets', 'Crypto.Cipher.AES', 'cryptography', 'sqlite3', 'json', 'threading', 'datetime', 'collections', 'ctypes', 'struct', 'watchdog', 'winotify', 'zstandard', 'xmltodict', 'PIL', 'aiofiles', 'yara', 'psutil', 'wechat_decrypt_core'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\wangc\\Documents\\code\\python\\WxGuiNotifier\\src\\img\\WeChat.ico'],
)
