@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYTHON_EXE="
set "PYTHON_ARGS="
set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

echo ========================================
echo   声文智汇 (ShengWen) - 一键启动
echo ========================================
echo.

if exist "%VENV_PYTHON%" (
    set "PYTHON_EXE=%VENV_PYTHON%"
) else (
    where python >nul 2>nul
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
    ) else (
        where py >nul 2>nul
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=py"
            set "PYTHON_ARGS=-3"
        )
    )
)

if not defined PYTHON_EXE (
    echo ❌ 错误: 未找到可用 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

if not exist frontend\dist (
    echo ❌ 错误: 未找到前端构建目录 frontend\dist
    echo 请先执行 deploy一键部署.bat 完成构建。
    pause
    exit /b 1
)

echo 使用 Python: %PYTHON_EXE% %PYTHON_ARGS%
echo.
call "%PYTHON_EXE%" %PYTHON_ARGS% ShengWen-app.py
if errorlevel 1 (
    echo.
    echo ❌ 服务退出，返回码: %errorlevel%
)

pause
