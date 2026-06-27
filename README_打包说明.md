# WxGuiNotifier Windows 打包说明

本文档介绍如何将微信消息通知助手打包为 Windows 10/11 可运行的 `.exe` 文件。

> ⚠️ **重要前提**：PyInstaller **不支持跨平台打包**。必须在 **Windows 系统**上打包 Windows exe，在 Linux 上只能打包 Linux 二进制。因此请将本项目代码放到 Windows 电脑上执行打包。

---

## 一、环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 1809+ / Windows 11（64 位） |
| Python | 3.9 ~ 3.12（推荐 3.11，64 位） |
| 磁盘空间 | 至少 2 GB（虚拟环境 + 打包临时文件） |
| 内存 | 至少 4 GB |
| 杀毒软件 | 建议临时关闭或加入白名单（PyInstaller 生成的 exe 常被误报） |

Python 下载地址：<https://www.python.org/downloads/>
安装时务必勾选 **"Add Python to PATH"**。

---

## 二、打包方式一：一键脚本（推荐）

1. 把整个项目文件夹复制到 Windows 电脑，**路径不要含中文或空格**（例如 `D:\code\WxGuiNotifier`）。
2. 双击运行 `一键打包.bat`。
3. 脚本会自动完成：
   - 检测 Python
   - 检查项目文件完整性
   - 升级 pip
   - 安装依赖（`requirements.txt`）
   - 调用 PyInstaller 打包
4. 打包完成后，`dist\` 目录会自动打开，里面有 `WxGuiNotifier.exe`。

---

## 三、打包方式二：命令行手动打包

### 步骤 1：安装依赖

打开 **PowerShell** 或 **CMD**，进入项目根目录：

```powershell
cd D:\code\WxGuiNotifier

# 创建虚拟环境（可选但推荐）
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 步骤 2：执行打包

**方式 A — 使用 spec 文件（推荐，配置最完整）**：

```powershell
pyinstaller WxGuiNotifier.spec --noconfirm --clean
```

**方式 B — 使用 build.py 脚本**：

```powershell
python build.py
```

**方式 C — 直接调用 PyInstaller（不推荐，参数较多）**：

```powershell
pyinstaller --name WxGuiNotifier ^
    --windowed --onefile ^
    --icon src\img\WeChat.ico ^
    --add-data "src;src" ^
    --add-data "core;core" ^
    --add-data "utils;utils" ^
    --add-data "ui;ui" ^
    --add-data "gui_config.example.json;." ^
    --add-data "version.json;." ^
    --hidden-import PyQt5 ^
    --hidden-import qfluentwidgets ^
    --collect-all qfluentwidgets ^
    --hidden-import utils.avatar_cache ^
    --hidden-import utils.gui_config ^
    --hidden-import core.wechat_decrypt_core ^
    --hidden-import core.wx_decrypt ^
    --hidden-import winotify ^
    --hidden-import watchdog ^
    --hidden-import PIL ^
    --noconfirm --clean ^
    wx_gui_notifier.py
```

### 步骤 3：获取产物

打包完成后，在 `dist\` 目录下：

| 文件 | 说明 |
|------|------|
| `WxGuiNotifier.exe` | 主程序，可直接分发 |
| `WxGuiNotifier.sha256` | SHA256 校验和 |
| `build_info.json` | 构建信息（版本、时间、Python 版本） |

---

## 四、制作安装包（可选，需 Inno Setup）

如果想生成带快捷方式、自动检查 VC++ 运行库的专业安装包：

1. 下载安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)。
2. 执行：

   ```powershell
   python build_msi.py
   ```

3. 生成的安装包在 `installer_output\WxGuiNotifier_Setup_1.0.0.exe`。

---

## 五、首次运行配置

打包出的 exe 首次在目标电脑运行时：

1. **以管理员身份运行**（右键 → 以管理员身份运行），否则无法读取微信进程内存提取密钥。
2. 程序会自动扫描微信数据目录并生成配置文件 `gui_config.json`。
3. 自动提取微信密钥到 `all_keys.json`（需微信已登录且进程在运行）。
4. 在主界面打开「服务状态」开关即可开始监听消息。

配置文件实际存储位置：

```
%LOCALAPPDATA%\WxGuiNotifier\gui_config.json
```

头像缓存位置（运行时自动生成）：

```
<数据缓存目录>\avatars\<wxid>.png
```

---

## 六、打包配置说明（本次更新内容）

相比原仓库，本次更新修复/补充了以下打包配置：

### 1. `WxGuiNotifier.spec`
- **修复硬编码绝对路径**：原文件写死 `C:\Users\wangc\...`，改成基于 `SPEC` 变量的相对路径，任意机器可用。
- **新增 `utils.avatar_cache`** 到 `hiddenimports`（本次新增的头像下载模块，必须显式声明否则运行时报 `ModuleNotFoundError`）。
- **补全 `utils.key_extractor`、`utils.key_scan_common`** 两个工具模块。
- **使用 `collect_data_files` / `collect_submodules`** 自动收集 qfluentwidgets、PyQt5、winotify 的所有数据文件和子模块，避免遗漏内部组件。
- **补充 `gui_config.example.json`、`version.json`、`LICENSE`** 到 `datas`。
- **动态检测 `all_keys.json`**：存在则一并打包，首次运行可省去提取步骤。
- **显式声明标准库** `urllib.request`、`urllib.error`、`io`、`struct`、`ctypes`（头像下载模块用到的）。
- **排除不必要的大库** `tkinter`、`matplotlib`、`numpy` 等，减小体积。
- **UPX 压缩排除 Qt 核心 DLL**，避免压缩导致 DLL 损坏。

### 2. `build.py` / `build_msi.py`
- 同步加入 `utils.avatar_cache` 等 hidden-imports。
- 文件完整性校验新增 `utils/avatar_cache.py`、`version.json`。
- 动态追加 `all_keys.json`、`gui_config.example.json`、`version.json` 到 add-data。
- 增加 `--collect-all qfluentwidgets` 确保组件完整。

### 3. `一键打包.bat`
- 新增增强版一键脚本，自动检测 Python、检查文件、安装依赖、打包、打开输出目录。
- 内置常见错误提示（杀软拦截、路径含中文等）。

---

## 七、常见问题

### Q1：打包时报 `ModuleNotFoundError: No module named 'utils.avatar_cache'`
**原因**：使用了旧的打包配置，未包含新增模块。
**解决**：使用本次更新的 `WxGuiNotifier.spec` 或 `build.py` 重新打包。

### Q2：生成的 exe 运行后无界面、秒退
**原因**：通常是缺 DLL 或依赖。
**解决**：
- 在 CMD 中运行 `WxGuiNotifier.exe`（不要双击），查看错误输出。
- 确认目标电脑已安装 [Visual C++ 2015-2022 x64 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
- 重新以 `--clean` 打包。

### Q3：杀毒软件报毒
PyInstaller 生成的单文件 exe 常被误报。解决方案：
- 加入杀软白名单。
- 或改用 `--onedir` 模式（修改 spec 中 `--onefile` 为目录模式），误报率更低。
- 或使用代码签名证书对 exe 签名。

### Q4：exe 体积过大（~80-120MB）
主要来自 PyQt5 + qfluentwidgets + cryptography。如需精简：
- 在 spec 的 `excludes` 中继续添加不用的库。
- 改用 `--onedir` 模式，首次启动更快。
- 启用 UPX 压缩（已在 spec 中启用）。

### Q5：打包后头像功能不工作
检查：
1. 目标电脑能正常访问 `wx.qlogo.cn`（微信头像 CDN）。
2. 首次运行后查看数据缓存目录下是否生成 `avatars\` 子目录。
3. 查看程序日志是否有「头像下载失败」相关提示。
4. 确认 `Pillow` 已正确打包（日志中不应出现 `Pillow 未安装` 字样）。

### Q6：打包后免打扰过滤不生效
检查：
1. 程序界面「过滤消息免打扰」开关是否打开。
2. 查看日志是否有「SessionTable 免打扰字段识别为：xxx」字样（确认字段自省成功）。
3. 不同微信小版本的字段名可能不同，如自省失败请反馈实际字段名。

---

## 八、打包产物分发清单

打包完成后，分发给最终用户时建议包含：

```
WxGuiNotifier_v1.0.0.zip
├── WxGuiNotifier.exe          # 主程序
├── WxGuiNotifier.sha256       # 校验和
├── gui_config.example.json    # 配置示例
├── README_打包说明.md          # 本文档
└── LICENSE                    # 开源协议
```

最终用户只需双击 `WxGuiNotifier.exe` 即可运行，无需安装 Python。

---

## 九、版本更新后重新打包

代码改动后，重新打包只需：

```powershell
# 清理旧产物
rmdir /s /q dist build

# 重新打包
pyinstaller WxGuiNotifier.spec --noconfirm --clean
```

或直接再次双击 `一键打包.bat`。
