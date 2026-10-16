"""读取 creds.env 后以 __main__ 方式运行 auto_checkin（给 xmu_watchdog.bat 用）。

数据目录由 paths.py 统一解析（便携模式=程序目录，安装模式=%APPDATA%）。
"""
import os
import runpy
from pathlib import Path

BASE = Path(__file__).resolve().parent
try:
    from paths import DATA_DIR as DATA_BASE
except Exception:
    DATA_BASE = BASE

creds = DATA_BASE / "creds.env"
if creds.exists():
    for ln in creds.read_text(encoding="utf-8-sig").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ[k.strip()] = v.strip()

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
runpy.run_path(str(BASE / "auto_checkin.py"), run_name="__main__")
