# WxGuiNotifier 编译指南

## 快速编译

### 方法一：双击运行批处理文件

1. 双击 `build.bat` 文件
2. 等待编译完成
3. 编译后的 exe 文件在 `dist` 目录中

### 方法二：命令行运行

```bash
# 在项目根目录执行
python build.py
```

## 编译输出

- **输出目录**: `dist/`
- **可执行文件**: `dist/WxGuiNotifier.exe`
- **文件大小**: 约 53 MB

## 功能特性

### 系统托盘支持

✅ **开机自启动后自动最小化到托盘**
- 程序启动后不会显示主窗口，直接最小化到系统托盘
- 双击托盘图标可打开主窗口
- 右键托盘图标可显示菜单（显示窗口、启动/停止服务、退出）

✅ **关闭窗口时自动最小化到托盘**
- 点击关闭按钮时，如果服务正在运行，会自动最小化到托盘
- 托盘会显示提示消息
- 双击托盘图标可恢复窗口

✅ **后台运行**
- 程序可以在系统托盘中后台运行
- 不占用任务栏空间
- 不影响其他工作

### 解决闪退问题

✅ **启动时不显示窗口**
- 避免了窗口初始化时可能的闪退问题
- 启动后显示托盘通知提示
- 用户可以根据需要打开主窗口

## 编译选项说明

编译脚本已配置以下选项：

- `--windowed`: 不显示控制台窗口（GUI 模式）
- `--onefile`: 打包为单个 exe 文件
- `--icon`: 使用微信图标 (`src/img/WeChat.ico`)
- `--add-data`: 包含必要的资源文件（wechat-decrypt、src 目录）
- `--hidden-import`: 包含必要的依赖库

## 手动编译（高级）

如果需要自定义编译选项，可以直接运行 PyInstaller：

```bash
pyinstaller --name WxGuiNotifier ^
            --windowed ^
            --onefile ^
            --icon src\img\WeChat.ico ^
            --add-data "wechat-decrypt;wechat-decrypt" ^
            --add-data "src;src" ^
            wx_gui_notifier.py
```

## 常见问题

### Q: 编译失败，提示找不到图标文件
A: 确保 `src/img/WeChat.ico` 文件存在

### Q: 编译后的程序运行报错
A: 检查是否包含了所有必要的依赖，查看 `build/WxGuiNotifier/warn-WxGuiNotifier.txt` 警告文件

### Q: 如何减小编译后的文件大小
A: 
1. 移除不需要的 `--hidden-import`
2. 使用虚拟环境，只安装必要的依赖
3. 使用 UPX 压缩（需要额外安装 UPX）

## 依赖库

编译脚本会自动安装 PyInstaller，其他依赖请确保已安装：

```bash
pip install -r requirements.txt
```

## 清理构建文件

如果需要清理旧的构建文件，可以手动删除以下目录：

- `build/` - 临时构建文件
- `dist/` - 输出文件
- `WxGuiNotifier.spec` - PyInstaller 配置文件

或者重新运行 `build.bat`，它会自动清理这些文件。
