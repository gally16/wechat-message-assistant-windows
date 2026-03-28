# WxGuiNotifier MSI 安装包创建指南

本指南将帮助你创建专业的 MSI 安装包，自动检查并安装所有依赖。

## 📋 系统要求

### 开发环境（打包机器）
- Windows 10/11 (64 位)
- Python 3.8+ 
- PyInstaller
- **Inno Setup 6.x** (必需)

### 目标环境（运行机器）
- Windows 10/11 (64 位)
- 已安装并登录微信
- 管理员权限（用于安装）

## 🚀 快速开始

### 1. 安装 Inno Setup

Inno Setup 是免费的安装包制作工具：

**下载地址**：https://jrsoftware.org/isdl.php

选择最新版本（Inno Setup 6.x）下载并安装。

### 2. 安装 Python 依赖

```bash
# 升级 pip
python -m pip install --upgrade pip

# 安装所有依赖
pip install -r requirements.txt
```

### 3. 创建 MSI 安装包

```bash
# 方法 1: 使用 MSI 打包脚本（推荐）
python build_msi.py

# 方法 2: 只打包 EXE（不创建 MSI）
python build_msi.py --only-exe

# 方法 3: 分步执行
# 3.1 先打包 EXE
python -m PyInstaller WxGuiNotifier.spec

# 3.2 再创建安装包
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" WxGuiNotifier.iss
```

## 📦 输出文件

打包完成后，会生成以下文件：

```
dist/
└── WxGuiNotifier.exe              # 独立可执行文件（约 60-80MB）

installer_output/
└── WxGuiNotifier_Setup_1.0.0.exe  # 安装包（约 60-80MB）
```

## 🔧 安装包功能

### 自动检查并安装依赖

安装包会自动检查以下组件，如果没有安装会自动下载并安装：

1. **Visual C++ Redistributable 2015-2022**
   - 自动检测是否已安装
   - 未安装时自动下载并静默安装
   - 下载地址：https://aka.ms/vs/17/release/vc_redist.x64.exe

2. **.NET Framework 4.7.2+**
   - 安装前检查版本
   - 如果版本过低会提示用户

### 自动创建快捷方式

- ✅ 开始菜单快捷方式
- ✅ 桌面快捷方式（可选）
- ✅ 快速启动栏快捷方式（可选）

### 自动注册卸载信息

安装包会自动在 Windows 控制面板中注册卸载信息，用户可以轻松卸载程序。

## 📝 自定义安装包

### 修改应用信息

编辑 `WxGuiNotifier.iss` 文件：

```pascal
#define MyAppName "WxGuiNotifier"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Your Company"
#define MyAppURL "https://your-website.com"
```

### 修改安装选项

编辑 `[Setup]` 部分：

```pascal
; 默认安装目录
DefaultDirName={autopf}\{#MyAppName}

; 默认组名
DefaultGroupName={#MyAppName}

; 要求管理员权限
PrivilegesRequired=admin

; 最低 Windows 版本
MinVersion=10.0.14393
```

### 添加额外文件

在 `[Files]` 部分添加：

```pascal
; 添加 README 文件
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; 添加配置文件示例
Source: "gui_config.example.json"; DestDir: "{app}"; Flags: ignoreversion
```

### 修改安装后行为

在 `[Run]` 部分添加：

```pascal
; 安装后启动程序
Filename: "{app}\{#MyAppExeName}"; Description: "启动程序"; Flags: nowait postinstall skipifsilent

; 打开网站
Filename: "https://your-website.com"; Description: "访问官网"; Flags: postinstall shellexec
```

## 🎯 打包流程详解

### 步骤 1: 打包 EXE

```bash
python -m PyInstaller WxGuiNotifier.spec
```

PyInstaller 会：
1. 分析所有导入的模块
2. 收集依赖的 DLL 和数据文件
3. 创建 bootloader
4. 打包成单个 EXE 文件

### 步骤 2: 编译安装脚本

```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" WxGuiNotifier.iss
```

Inno Setup 会：
1. 读取 `.iss` 脚本
2. 收集所有文件
3. 创建安装向导
4. 生成安装包

### 步骤 3: 验证安装包

安装包会自动：
1. 检查操作系统版本（需要 Windows 10+）
2. 检查 .NET Framework 版本
3. 检查 VC++ 运行库
4. 如果需要，下载并安装 VC++
5. 复制文件到安装目录
6. 创建快捷方式
7. 注册卸载信息

## ⚠️ 常见问题

### Q1: Inno Setup 找不到

**解决方案**：
1. 确保已安装 Inno Setup 6.x
2. 修改 `build_msi.py` 中的 `ISCC_PATH` 为实际路径
3. 或者手动运行 ISCC.exe

### Q2: VC++ 运行库安装失败

**解决方案**：
1. 手动下载安装：https://aka.ms/vs/17/release/vc_redist.x64.exe
2. 以管理员身份运行安装包
3. 检查 Windows Update 是否已禁用

### Q3: 安装包太大

**解决方案**：
1. 使用 `/COMPRESS` 参数优化压缩
2. 排除不必要的文件
3. 考虑使用在线安装方式

### Q4: 静默安装参数

**解决方案**：
```bash
# 完全静默安装
WxGuiNotifier_Setup_1.0.0.exe /VERYSILENT /NORESTART

# 显示进度但不交互
WxGuiNotifier_Setup_1.0.0.exe /SILENT /NORESTART

# 指定安装目录
WxGuiNotifier_Setup_1.0.0.exe /DIR="C:\MyApp"

# 不创建快捷方式
WxGuiNotifier_Setup_1.0.0.exe /MERGETASKS=!desktopicon
```

### Q5: 卸载程序

**解决方案**：
1. 控制面板 > 程序和功能 > WxGuiNotifier > 卸载
2. 或者运行：`"C:\Program Files\WxGuiNotifier\unins000.exe"`

## 📊 性能指标

典型的安装包：
- **EXE 大小**: 60-80 MB
- **安装包大小**: 60-80 MB（包含所有依赖）
- **安装时间**: 10-30 秒
- **安装后大小**: 150-200 MB

## 🔐 数字签名（可选）

如果需要提高可信度，可以对安装包进行数字签名：

```bash
# 使用 SignTool（需要证书）
signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com installer_output/WxGuiNotifier_Setup_1.0.0.exe

# 验证签名
signtool verify /pa installer_output/WxGuiNotifier_Setup_1.0.0.exe
```

## 📚 参考资源

- [Inno Setup 官方文档](https://jrsoftware.org/ishelp/)
- [Inno Setup 脚本语言参考](https://jrsoftware.org/ishelp/index.php?topic=languages)
- [PyInstaller 文档](https://pyinstaller.org/en/stable/)
- [Windows 安装包最佳实践](https://docs.microsoft.com/en-us/windows/win32/msi/installer)

## 📞 技术支持

如有问题，请：
1. 查看 Inno Setup 编译日志
2. 检查安装包日志（`%TEMP%\Setup Log*.txt`）
3. 联系开发者或在 GitHub 提交 Issue

---

**最后更新**: 2026-03-28
**版本**: 1.0.0
