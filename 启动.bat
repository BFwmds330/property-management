@echo off
rem ==================================================
rem  物业管家（个人版）一键启动脚本
rem  双击本文件即可启动系统，浏览器会自动打开
rem ==================================================
cd /d "%~dp0"
title 物业管家（个人版）
cls

echo  ┌────────────────────────────────────┐
echo  │       物业管家（个人版）启动中        │
echo  └────────────────────────────────────┘
echo.

rem ---- 第 1 步：寻找电脑上的 Python ----
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY call :find_local_python

if not defined PY (
  echo  [提示] 电脑上还没有安装 Python，请按下面步骤操作（只需一次）：
  echo.
  echo    1. 打开网址：https://www.python.org/downloads/
  echo    2. 点击黄色的按钮，下载并安装
  echo    3. 安装第一屏请务必勾选底部的 “Add Python to PATH”
  echo    4. 装完后，重新双击本文件即可
  echo.
  if defined WUYE_AUTOTEST exit /b 1
  pause
  exit /b 1
)

rem ---- 第 2 步：检查并安装依赖（只需一次）----
%PY% -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo  首次运行：正在安装运行环境，请稍等（需要联网，约半分钟）...
  %PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
  if errorlevel 1 (
    echo  默认源安装失败，改用国内镜像重试...
    %PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
      echo.
      echo  [提示] 安装失败，请检查网络连接后重新双击本文件
      if defined WUYE_AUTOTEST exit /b 1
      pause
      exit /b 1
    )
  )
  echo  安装完成！
  echo.
)

rem ---- 第 3 步：启动系统 ----
echo  正在启动，浏览器马上会自动打开系统首页...
echo  用完后直接关闭本窗口即可退出系统。
echo.
%PY% app.py
if errorlevel 1 (
  echo.
  echo  [提示] 启动失败了，请把上面窗口里的文字拍照留存
)
if defined WUYE_AUTOTEST exit /b 0
pause
exit /b 0

rem ---- 在常见安装位置自动寻找 Python ----
:find_local_python
for %%V in (314 313 312 311 310 39 38) do (
  if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if exist "C:\Python%%V\python.exe" set "PY=C:\Python%%V\python.exe"
  if exist "C:\Program Files\Python%%V\python.exe" set "PY=C:\Program Files\Python%%V\python.exe"
)
goto :eof
