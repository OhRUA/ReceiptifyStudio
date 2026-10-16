@echo off
chcp 65001 >nul
title Receiptify Studio
cd /d "%~dp0"

REM 检查虚拟环境是否就绪；缺失时给出提示而不是闪退
if not exist "venv\Scripts\pythonw.exe" (
    echo [错误] 虚拟环境不存在，请先运行 install_env.bat 安装依赖。
    pause
    exit /b
)

REM 无黑窗后台启动主程序（pythonw 不显示控制台窗口）
start "" "venv\Scripts\pythonw.exe" "main.py"
exit /b
