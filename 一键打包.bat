@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM  WxGuiNotifier 一键打包脚本（增强版）
REM  - 自动检测 Python
REM  - 自动创建虚拟环境（可选）
REM  - 自动安装依赖
REM  - 自动执行 PyInstaller 打包
REM  - 打包完成后显示输出文件位置
REM ============================================================

title WxGuiNotifier 打包工具

echo ============================================================
echo   WxGuiNotifier 一键打包工具
echo   微信消息通知助手 - Windows EXE 打包
echo ============================================================
echo.

REM ---- 1. 检查 Python ----
echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        echo [错误] 未检测到 Python，请先安装 Python 3.8+ (64位)
        echo        下载地址: https://www.python.org/downloads/
        echo        安装时请勾选 "Add Python to PATH"
        pause
        exit /b 1
    )
    set PYTHON_CMD=py -3
) else (
    set PYTHON_CMD=python
)
echo [成功] 使用: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

REM ---- 2. 检查项目文件完整性 ----
echo [2/5] 检查项目文件...
if not exist "wx_gui_notifier.py" (
    echo [错误] 未找到 wx_gui_notifier.py，请在项目根目录运行此脚本
    pause
    exit /b 1
)
if not exist "src\img\WeChat.ico" (
    echo [错误] 未找到图标文件 src\img\WeChat.ico
    pause
    exit /b 1
)
if not exist "utils\avatar_cache.py" (
    echo [错误] 未找到 utils\avatar_cache.py（头像模块缺失）
    pause
    exit /b 1
)
echo [成功] 项目文件完整
echo.

REM ---- 3. 升级 pip ----
echo [3/5] 升级 pip...
%PYTHON_CMD% -m pip install --upgrade pip -q
if errorlevel 1 (
    echo [警告] pip 升级失败，继续打包...
) else (
    echo [成功] pip 已升级
)
echo.

REM ---- 4. 安装依赖 ----
echo [4/5] 安装项目依赖（首次安装可能需要几分钟）...
%PYTHON_CMD% -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络或手动执行:
    echo        pip install -r requirements.txt
    pause
    exit /b 1
)
echo [成功] 依赖安装完成
echo.

REM ---- 5. 执行打包 ----
echo [5/5] 执行 PyInstaller 打包...
echo.
echo ============================================================
echo  开始打包，请耐心等待（通常 3-8 分钟）...
echo ============================================================
echo.

%PYTHON_CMD% build.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  [失败] 打包过程中出现错误
    echo  请查看上方日志，常见原因：
    echo   1. 杀毒软件拦截 PyInstaller（请临时关闭或加入白名单）
    echo   2. 依赖未完整安装（重新执行 pip install -r requirements.txt）
    echo   3. 路径含中文或空格（建议放在纯英文路径下）
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  打包完成！
echo ============================================================
echo.
echo  输出文件位置:
echo    dist\WxGuiNotifier.exe        (主程序)
echo    dist\WxGuiNotifier.sha256     (校验和)
echo    dist\build_info.json          (构建信息)
echo.
echo  使用方法:
echo    1. 将 dist\WxGuiNotifier.exe 复制到任意 Windows 10 电脑
echo    2. 确保该电脑已安装微信 4.0+ 并已登录
echo    3. 右键以管理员身份运行 WxGuiNotifier.exe
echo    4. 首次运行会自动提取微信密钥（需管理员权限）
echo.
echo  注意事项:
echo    - 目标电脑需 Windows 10 1809+ / Windows 11
echo    - 需 Visual C++ 2015-2022 运行库（x64）
echo    - 首次启动较慢（解压临时文件），属正常现象
echo ============================================================
echo.

REM 自动打开输出目录
if exist "dist\WxGuiNotifier.exe" (
    explorer "dist"
)

pause
endlocal
