@echo off
chcp 65001 >nul
echo ============================================================
echo WxGuiNotifier 编译脚本
echo ============================================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

echo [信息] 使用 Python: %PYTHON%
echo.

REM 运行编译脚本
python build.py

echo.
echo ============================================================
echo 编译完成！
echo ============================================================
echo.
pause
