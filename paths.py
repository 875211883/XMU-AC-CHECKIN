# -*- coding: utf-8 -*-
"""数据目录解析（程序文件 vs 用户数据分离）。

解析规则（按顺序）：

1. 环境变量 `XMU_DATA_DIR` 指定 → 用它（方便测试/多实例）。
2. **程序目录里已经有老数据**（creds.env / course_filter.json / 签到记录 / 会话等
   任意一个）→ 继续用程序目录。这样"把 exe 放哪数据就在哪"的老用法、
   以及已经部署在 D:\\签到\\XMU畅课签到 的实例**不受影响**。
3. 否则用 `%APPDATA%\\XMU-AC-CHECKIN`（安装版默认），目录会自动创建。

好处：安装版把程序装在 `%LOCALAPPDATA%\\Programs`，数据放 `%APPDATA%`，
**覆盖安装、卸载程序都不会丢配置**；而便携版（数据跟着 exe 走）依然可用。

被 auto_checkin.py / control_panel.py / launcher_worker.py 共用，
避免三处各写一套解析逻辑导致数据分家。
"""
import os
import sys
from pathlib import Path

#: 程序所在目录（exe 或源码目录）——只读的程序文件放这里
APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parent)

#: 安装版默认的数据目录名
DATA_DIR_NAME = "XMU-AC-CHECKIN"

#: 用于判断"程序目录里是否已有老数据"的标记文件
_LEGACY_MARKERS = (
    "creds.env",
    "course_filter.json",
    "checkin_record.json",
    "schedule.json",
    "auto_checkin_session.json",
    "auto_checkin_status.json",
)


def _has_legacy_data(d: Path) -> bool:
    for name in _LEGACY_MARKERS:
        try:
            if (d / name).exists():
                return True
        except OSError:
            pass
    return False


def _default_data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / DATA_DIR_NAME
    # 极端情况（APPDATA 缺失）退回家目录
    return Path.home() / f".{DATA_DIR_NAME.lower()}"


def _looks_like_source_checkout(d: Path) -> bool:
    """像是"源码仓库目录"而不是安装目录吗。

    判据：有版本控制目录（.git）或项目标志文件（XMU畅课签到.spec / requirements.txt）。

    为什么需要这条：源码模式跑 app.py 时，程序目录就是仓库；如果用户把仓库里的
    creds.env 清理掉（或从没建过），按"没有老数据就用 %APPDATA%"的规则会切到
    %APPDATA%，于是**源码模式与部署版的数据分家**。仓库目录永远优先用自己目录，
    行为更可预期；安装版装在 %LOCALAPPDATA%\\Programs，那里没有这些标志文件，
    仍然走 %APPDATA%，所以两者不冲突。
    """
    try:
        if (d / ".git").exists():
            return True
        for name in ("XMU畅课签到.spec", "requirements.txt", "paths.py"):
            if (d / name).exists():
                return True
    except OSError:
        pass
    return False


def resolve_data_dir() -> Path:
    env = (os.environ.get("XMU_DATA_DIR") or "").strip()
    if env:
        d = Path(env).expanduser()
    elif _has_legacy_data(APP_DIR) or _looks_like_source_checkout(APP_DIR):
        d = APP_DIR
    else:
        d = _default_data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = APP_DIR          # 万无一失：退回程序目录
    return d


#: 用户数据目录（creds.env、签到记录、日志、状态…都在这）
DATA_DIR = resolve_data_dir()


def data_path(*parts) -> Path:
    """拼一个数据文件路径：data_path("creds.env")"""
    return DATA_DIR.joinpath(*parts)


def describe() -> str:
    """给人看的一行说明（排障/界面用）"""
    kind = "程序目录（便携模式）" if DATA_DIR == APP_DIR else "用户数据目录"
    return f"{kind}: {DATA_DIR}"
