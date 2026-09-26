# -*- coding: utf-8 -*-
"""全局配置：路径、数据库文件位置等。"""
import os
import sys

# 项目根目录（本文件所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据目录：所有数据（数据库、备份）都保存在这里，绝不放到别处
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")   # 报修照片等上传文件（v2.3.0）

# SQLite 数据库文件（主数据文件）
DB_PATH = os.path.join(DATA_DIR, "property.db")

# 服务默认端口；被占用时会自动往后找空闲端口（5000~5010）
DEFAULT_PORT = 5000

# 恢复数据上传大小限制：100MB 足够了
MAX_CONTENT_LENGTH = 100 * 1024 * 1024

# 启动时自动备份最多保留多少份（超出后删最旧的）
AUTO_BACKUP_KEEP = 14


def ensure_dirs():
    """确保数据目录存在（首次运行时自动创建）。"""
    for d in (DATA_DIR, BACKUP_DIR, UPLOAD_DIR):
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except OSError:
                # 万一目录建不出来（比如只读盘），把错误直接报给用户看
                print("【错误】无法创建数据目录：%s" % d, file=sys.stderr)
                raise


def load_edition():
    """读取版本标识：项目根目录下若有 edition.txt，其内容作为版本名（如“自用版”）。

    标识只影响界面显示。放在独立文件里而不是代码里，是为了以后升级
    （覆盖代码文件）时不会丢失身份标记。
    """
    path = os.path.join(BASE_DIR, "edition.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            name = f.read().strip()
        return name[:12]
    except OSError:
        return ""


# 版本标识（空 = 标准版）
EDITION = load_edition()

# 系统版本号：每次发布更新，更新日志见 CHANGELOG.md
VERSION = "2.3.0"
