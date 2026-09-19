#!/bin/bash
# ==================================================
#  物业管家（个人版）一键启动脚本（Mac 版）
#  双击本文件（或在终端里运行 ./启动.command）即可启动
# ==================================================
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo " [提示] 电脑上还没有安装 Python3，请先安装："
  echo "   1. 打开 https://www.python.org/downloads/"
  echo "   2. 下载并安装（Mac 版）"
  echo "   3. 装完后重新运行本文件"
  echo
  read -n 1 -s -r -p "按任意键退出..."
  exit 1
fi

if ! python3 -c "import flask" >/dev/null 2>&1; then
  echo " 首次运行：正在安装运行环境，请稍等（需要联网，约半分钟）..."
  python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check \
    || python3 -m pip install -r requirements.txt --quiet -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo " 安装完成！"
fi

echo " 正在启动，浏览器马上会自动打开系统首页..."
python3 app.py
