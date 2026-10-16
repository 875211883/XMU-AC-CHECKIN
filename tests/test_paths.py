# 离线单测：数据目录解析（程序文件 vs 用户数据分离）
#
# 关键回归点：**老部署（数据在程序目录）不能因为引入安装版而"数据分家"** ——
# 否则用户会遇到"配置突然丢了/要重新登录"。
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def fresh_paths(app_dir: Path, appdata: Path, env_dir=None):
    """在隔离环境里重新导入 paths，返回模块。

    注意：paths.DATA_DIR 是 import 时计算的模块级常量，所以模拟"程序目录"后
    必须把它一起重算，否则测的是真实项目目录（测试桩的坑，不是产品 bug）。
    """
    old = {k: os.environ.get(k) for k in ("APPDATA", "XMU_DATA_DIR")}
    os.environ["APPDATA"] = str(appdata)
    if env_dir:
        os.environ["XMU_DATA_DIR"] = str(env_dir)
    else:
        os.environ.pop("XMU_DATA_DIR", None)
    for m in ("paths",):
        sys.modules.pop(m, None)
    import paths
    paths.APP_DIR = app_dir          # 模拟"程序所在目录"
    paths.DATA_DIR = paths.resolve_data_dir()
    return paths, old


def restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    sys.modules.pop("paths", None)


tmp = Path(tempfile.mkdtemp(prefix="xmu_paths_"))
app_clean = tmp / "app_clean"
app_legacy = tmp / "app_legacy"
appdata = tmp / "appdata"
for d in (app_clean, app_legacy, appdata):
    d.mkdir(parents=True, exist_ok=True)

print("== 场景 1：干净的程序目录（安装版）→ 数据放 %APPDATA% ==")
p, old = fresh_paths(app_clean, appdata)
d = p.resolve_data_dir()
check("落到 APPDATA\\XMU-AC-CHECKIN",
      d == appdata / "XMU-AC-CHECKIN", str(d))
check("目录被自动创建", d.exists())
check("describe 说明是用户数据目录", "用户数据目录" in p.describe())
check("data_path 拼接正确",
      p.data_path("creds.env") == d / "creds.env")
restore(old)

print("== 场景 2：程序目录里已有老数据（便携/老部署）→ 原地继续用 ==")
(app_legacy / "creds.env").write_text("STUDENT_ID=x\nPASSWORD=y\n",
                                      encoding="utf-8")
p, old = fresh_paths(app_legacy, appdata)
d = p.resolve_data_dir()
check("数据目录 = 程序目录（不分家）", d == app_legacy, str(d))
check("describe 说明是便携模式", "便携模式" in p.describe())
restore(old)

print("== 场景 3：老数据只有签到记录（没有 creds.env）也要认 ==")
(app_legacy / "creds.env").unlink()
(app_legacy / "checkin_record.json").write_text("[]", encoding="utf-8")
p, old = fresh_paths(app_legacy, appdata)
check("凭记录文件也判定为老部署",
      p.resolve_data_dir() == app_legacy, str(p.resolve_data_dir()))
restore(old)

print("== 场景 4：环境变量 XMU_DATA_DIR 优先 ==")
custom = tmp / "custom_data"
p, old = fresh_paths(app_legacy, appdata, env_dir=custom)
check("按环境变量走", p.resolve_data_dir() == custom, str(p.resolve_data_dir()))
check("自定义目录被创建", custom.exists())
restore(old)

print("== 场景 5：环境变量指向已存在目录（老数据在别处） ==")
p, old = fresh_paths(app_clean, appdata, env_dir=app_legacy)
check("老部署可以用 XMU_DATA_DIR 指向旧目录",
      p.resolve_data_dir() == app_legacy)
restore(old)

print("== 场景 6：APPDATA 缺失时不炸 ==")
old_appdata = os.environ.pop("APPDATA", None)
sys.modules.pop("paths", None)
import paths as p6  # noqa: E402
check("退回家目录而不是抛异常", ".xmu-ac-checkin" in str(p6._default_data_dir()))
if old_appdata is not None:
    os.environ["APPDATA"] = old_appdata
sys.modules.pop("paths", None)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
