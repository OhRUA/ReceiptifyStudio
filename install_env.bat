@echo off
chcp 65001 >nul
title Receiptify Studio - 环境安装
cd /d "%~dp0"

REM 用 Anaconda 的 Python 创建虚拟环境（完整 tkinter，GUI 必需）
set ANACONDA_PY=D:\Software\anaconda3\python.exe
if not exist "%ANACONDA_PY%" (
    echo [错误] 找不到 Anaconda Python：%ANACONDA_PY%
    echo 请修改本文件第一行的 ANACONDA_PY 为你的 python.exe 路径。
    pause
    exit /b
)

echo [1/2] 创建虚拟环境...
"%ANACONDA_PY%" -m venv venv

echo [2/2] 安装依赖（首次约 1-2 分钟）...
"venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo 完成！双击「启动Receiptify.bat」即可运行。
pause
