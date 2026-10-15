# -*- coding: utf-8 -*-
"""
XMU-AC 签到控制台
- 查看进程状态 / 最近签到事件
- 启动 / 停止签到进程
用法: python control_panel.py   (双击桌面快捷方式即可)
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

if sys.platform == "win32":
    import msvcrt
from datetime import datetime, timedelta
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# 打包成 exe 后，数据文件存放在 exe 同目录（而非临时解压目录）
if FROZEN:
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent
LOG_FILE = BASE / "checkin.log"
PID_FILE = BASE / "auto_checkin.pid"
RECORD_FILE = BASE / "checkin_record.json"
SCHEDULE_FILE = BASE / "schedule.json"
COURSE_FILE = BASE / "course_filter.json"
START_FILE = BASE / "start_time.txt"
LAUNCH_FILE = BASE / "launch_count.json"
RUN_STATUS_FILE = BASE / "auto_checkin_status.json"
# Windows 任务计划里的两个任务名（定时启动 / 定时关闭）
TASK_START = "XMU-AC-AutoStart"
TASK_STOP = "XMU-AC-AutoStop"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def load_json_file(path, default):
    """读取 JSON 文件，失败/不存在时返回 default。
    用 utf-8-sig 读：某些编辑器/旧版本写出的文件带 BOM，
    普通 utf-8 解析会直接抛 JSONDecodeError（表现为界面读不到已保存的设置）。"""
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return default


def _resolve_python():
    """挑选能跑签到的解释器：优先项目虚拟环境，其次当前解释器。

    不要写死本机绝对路径（换台机器就找不到，也会把个人路径带进仓库）。
    脚本模式下用当前解释器最稳 —— 依赖本来就是装在它下面的。
    """
    candidates = [
        BASE / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path(sys.executable)


PYTHON = _resolve_python()
# 无控制台窗口的解释器，供任务计划在后台静默执行 start/stop
PYTHONW = PYTHON.with_name("pythonw.exe")
if not PYTHONW.exists():
    PYTHONW = PYTHON

# ============================================================
# Win32 直呼（ctypes）：进程探测/控制台判断
# 目的：本程序是 GUI(console=False) 进程，只要启动任何"没隐藏控制台"的
# 子进程（os.system / 不带 CREATE_NO_WINDOW 的 subprocess），Windows 就会
# 新开一个控制台窗口 —— 在把 Windows Terminal 设为默认终端的机器上表现为
# "黑色窗口每隔几秒闪一次"。所以刷新循环里一律不启动子进程。
# ============================================================
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _NTDLL = ctypes.WinDLL("ntdll")
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _STILL_ACTIVE = 259
    _TH32CS_SNAPPROCESS = 0x00000002
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT),
                    ("MaximumLength", wintypes.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("Reserved1", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("Reserved2", ctypes.c_void_p * 2),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("Reserved3", ctypes.c_void_p)]

    _K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.CloseHandle.argtypes = [wintypes.HANDLE]
    _K32.CloseHandle.restype = wintypes.BOOL
    _K32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.DWORD)]
    _K32.GetExitCodeProcess.restype = wintypes.BOOL
    _K32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
    _K32.ReadProcessMemory.restype = wintypes.BOOL
    _K32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _K32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _K32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(_PROCESSENTRY32W)]
    _K32.Process32FirstW.restype = wintypes.BOOL
    _K32.Process32NextW.argtypes = [wintypes.HANDLE,
                                    ctypes.POINTER(_PROCESSENTRY32W)]
    _K32.Process32NextW.restype = wintypes.BOOL
    _K32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    _K32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _K32.GetConsoleWindow.restype = wintypes.HWND
    _NTDLL.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p,
                                                 wintypes.ULONG,
                                                 ctypes.POINTER(wintypes.ULONG)]
    _NTDLL.NtQueryInformationProcess.restype = ctypes.c_long


def _has_console():
    """当前进程是否挂着真实控制台（GUI/无控制台 → False）"""
    if sys.platform != "win32":
        return False
    try:
        return bool(_K32.GetConsoleWindow())
    except Exception:
        return False


def _spawn_flags(extra=0):
    """子进程创建标志：始终隐藏控制台窗口，避免闪黑窗"""
    if sys.platform != "win32":
        return 0
    return _CREATE_NO_WINDOW | extra


def _clear_screen():
    """清屏。只在真的挂着控制台窗口时执行 —— GUI/无控制台时 os.system
    会新开一个控制台窗口（黑窗一闪），必须跳过。"""
    try:
        if sys.platform == "win32":
            if _has_console():
                os.system("cls")
        else:
            os.system("clear")
    except Exception:
        pass


def _proc_path(pid):
    """进程映像全路径（取不到返回空串）"""
    if sys.platform != "win32":
        return ""
    h = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if _K32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    except Exception:
        return ""
    finally:
        _K32.CloseHandle(h)


def _proc_cmdline(pid):
    """读取进程命令行（PEB 方式，纯 ctypes，不启动任何子进程）"""
    if sys.platform != "win32":
        return ""
    h = _K32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ,
                         False, int(pid))
    if not h:
        return ""
    try:
        pbi = _PROCESS_BASIC_INFORMATION()
        if _NTDLL.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                            ctypes.sizeof(pbi),
                                            None) != 0:
            return ""
        peb = pbi.PebBaseAddress
        if not peb:
            return ""
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        read = ctypes.c_size_t()
        params = ctypes.c_void_p()
        if not _K32.ReadProcessMemory(
                h, ctypes.c_void_p(peb + (0x20 if ptr_size == 8 else 0x10)),
                ctypes.byref(params), ptr_size, ctypes.byref(read)):
            return ""
        if not params.value:
            return ""
        us = _UNICODE_STRING()
        if not _K32.ReadProcessMemory(
                h, ctypes.c_void_p(params.value + (0x70 if ptr_size == 8
                                                   else 0x40)),
                ctypes.byref(us), ctypes.sizeof(us), ctypes.byref(read)):
            return ""
        if not us.Buffer or not us.Length:
            return ""
        raw = ctypes.create_string_buffer(us.Length)
        if not _K32.ReadProcessMemory(h, ctypes.c_void_p(us.Buffer), raw,
                                      us.Length, ctypes.byref(read)):
            return ""
        return raw.raw[:us.Length].decode("utf-16-le", "replace")
    except Exception:
        return ""
    finally:
        _K32.CloseHandle(h)


def _iter_named_procs(names):
    """枚举指定映像名的进程 → (pid, exe 全路径)"""
    if sys.platform != "win32":
        return
    snap = _K32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _K32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            if pid and entry.szExeFile.lower() in names:
                yield pid, _proc_path(pid)
            ok = _K32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _K32.CloseHandle(snap)


# Windows 控制台切到 UTF-8，保证中文不乱码。
# 注意：这里绝不能用 os.system("chcp ...")，那会新建一个控制台窗口。
# 只在确实挂着控制台时，用隐藏窗口的子进程切换代码页。
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if _has_console():
        try:
            subprocess.run(["cmd", "/c", "chcp 65001"],
                           stdin=subprocess.DEVNULL, capture_output=True,
                           creationflags=_CREATE_NO_WINDOW, timeout=10)
        except Exception:
            pass


def now():
    return datetime.now().strftime("%H:%M:%S")


def _run(args, **kw):
    """统一的子进程调用：自动加 CREATE_NO_WINDOW，
    避免 GUI(pythonw) 无控制台时弹出黑窗；并兼容中文系统 GBK 输出。"""
    kw.pop("text", None)
    kw.pop("encoding", None)
    kw.pop("errors", None)
    kw.setdefault("capture_output", True)
    kw.setdefault("stdin", subprocess.DEVNULL)
    if sys.platform == "win32":
        kw["creationflags"] = kw.get("creationflags", 0) | _CREATE_NO_WINDOW
    r = subprocess.run(args, **kw)

    def _dec(b):
        if b is None:
            return ""
        if isinstance(b, str):
            return b
        for enc in ("utf-8", "gbk"):
            try:
                return b.decode(enc)
            except Exception:
                pass
        return b.decode("gbk", errors="replace")

    r.stdout = _dec(r.stdout)
    r.stderr = _dec(r.stderr)
    return r


def _heartbeat_age():
    """签到进程最近一轮轮询距今的秒数（拿不到返回 None）"""
    ts = run_status().get("updated_at")
    if not ts:
        return None
    try:
        t = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return (datetime.now() - t).total_seconds()


def _checkin_marks(pid):
    """该 pid 是否为本程序的签到监控进程
    （命令行含 auto_checkin / --checkin）；读不到命令行返回 None。"""
    cl = _proc_cmdline(pid)
    if not cl:
        return None
    low = cl.lower()
    return ("auto_checkin" in low) or ("--checkin" in low)


def find_running():
    """返回正在运行的签到监控进程 PID，找不到返回 None。

    ⚠️ 这个函数会被 GUI 每 3 秒调用一次，**绝不能启动子进程**
    （tasklist/powershell 都是控制台程序，会让黑色控制台窗口反复闪现）。
    因此改为纯 Win32 调用：PID 文件 → 命令行匹配 → 心跳兜底。
    """
    me = os.getpid()
    # 1) PID 文件记录的进程（start() 写入，最准）
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except Exception:
            pid = 0
        if pid and pid != me and _pid_alive(pid) and _checkin_marks(pid) is not False:
            return pid
    # 2) 兜底：按映像名扫描系统进程
    #    打包后是本程序 exe；脚本模式是 python / pythonw
    names = ({Path(sys.executable).name.lower()} if FROZEN
             else {"python.exe", "pythonw.exe"})
    unknown = None
    for pid, _path in _iter_named_procs(names):
        if pid == me:
            continue
        mark = _checkin_marks(pid)
        if mark:
            return pid
        if mark is None and unknown is None:
            unknown = pid
    # 3) 命令行读不到（权限受限）时，用状态文件心跳判断是否真在监控中
    if unknown is not None:
        age = _heartbeat_age()
        if age is not None and 0 <= age <= 90:
            return unknown
    return None


def _pid_alive(pid):
    """进程是否仍存活（纯 Win32 / os.kill，不启动 tasklist）"""
    if sys.platform != "win32":
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False
    h = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not _K32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    except Exception:
        return False
    finally:
        _K32.CloseHandle(h)


def _load_creds():
    """读取 creds.env 里的 STUDENT_ID / PASSWORD，不存在返回 {}"""
    cfg = {}
    f = BASE / "creds.env"
    if f.exists():
        for ln in f.read_text(encoding="utf-8-sig",
                              errors="replace").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def current_account():
    """返回已保存的学号（账号名），没有返回空串"""
    return _load_creds().get("STUDENT_ID", "")


def save_creds(student_id, password):
    """把账号密码写入 creds.env（自动保存，下次免输入）。
    密码留空时保留原密码不变。"""
    sid = (student_id or "").strip()
    if not sid:
        return "请输入学号"
    old = _load_creds()
    pwd = password or ""
    if not pwd:
        pwd = old.get("PASSWORD", "")
    if not pwd:
        return "请输入密码"
    lines = [f"STUDENT_ID={sid}", f"PASSWORD={pwd}"]
    (BASE / "creds.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"账号 {sid} 已保存"


# ============================================================
# 课程勾选：只监控自己真正在上的课（默认全部监控，只记"不监控"的例外）
# ============================================================

def course_filter_load():
    """读取课程筛选，返回 {"disabled": ["课程id", ...]}"""
    d = load_json_file(COURSE_FILE, {})
    ids = d.get("disabled") if isinstance(d, dict) else None
    if not isinstance(ids, list):
        ids = []
    return {"disabled": [str(x) for x in ids]}


def course_filter_save(disabled):
    """保存未勾选的课程 id，返回排除数量"""
    ids = []
    for x in disabled or []:
        s = str(x).strip()
        if s and s not in ids:
            ids.append(s)
    COURSE_FILE.write_text(
        json.dumps({"disabled": ids}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return len(ids)


def filter_courses(courses):
    """按勾选过滤课程列表（未勾选的课完全不抓取）"""
    disabled = set(course_filter_load()["disabled"])
    return [c for c in (courses or []) if str(c.get("id")) not in disabled]


def course_monitor_count(courses):
    """返回 (监控门数, 总门数)"""
    courses = courses or []
    return len(filter_courses(courses)), len(courses)


def start():
    """后台启动签到进程（注入 creds.env 凭据），输出写入 checkin.log"""
    if find_running():
        return "已经在运行，无需重复启动"
    env = os.environ.copy()
    env.update(_load_creds())
    if not env.get("STUDENT_ID") or not env.get("PASSWORD"):
        return "缺少账号: 请编辑同目录 creds.env 填入 STUDENT_ID / PASSWORD"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 子进程输出重定向到文件后默认是块缓冲，日志会长时间看不到内容 ——
    # 强制不缓冲，崩溃/异常才能及时落到 checkin.log 里。
    env["PYTHONUNBUFFERED"] = "1"
    # 日志过大先清空，避免无限增长
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
            LOG_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    logf = open(LOG_FILE, "a", encoding="utf-8")
    if FROZEN:
        # 剔除会干扰子 onefile 的环境变量：
        # _PYI_* 让子 bootloader 误判自身层级；沙箱注入的 PYTHONPATH /
        # PYTHONPYCACHEPREFIX / TCL|TK_LIBRARY 会污染模块与 Tk 搜索路径。
        for k in list(env.keys()):
            if (k.startswith("_PYI") or k in
                    ("PYTHONPATH", "PYTHONPYCACHEPREFIX", "TCL_LIBRARY",
                     "TK_LIBRARY", "PYTHONNOUSERSITE", "PYTHONUNBUFFERED")):
                env.pop(k, None)
        # 单文件 windowed exe：本身不会开控制台窗口；再加 CREATE_NO_WINDOW 双保险。
        # 输出写入 checkin.log —— 打包后没有控制台，异常只能靠日志排查。
        cmd = [sys.executable, "--checkin"]
        p = subprocess.Popen(cmd, cwd=str(BASE),
                             stdin=subprocess.DEVNULL,
                             stdout=logf, stderr=subprocess.STDOUT,
                             env=env, creationflags=_spawn_flags())
    else:
        # CREATE_NO_WINDOW：后台运行、有隐藏控制台，使脚本里 os.system("cls")
        # 在隐藏控制台上执行，不会每秒弹出 cmd 黑窗；不能与 DETACHED_PROCESS 同用。
        cmd = [str(PYTHON), "-u", "auto_checkin.py"]
        p = subprocess.Popen(
            cmd, cwd=str(BASE), stdin=subprocess.DEVNULL,
            stdout=logf, stderr=subprocess.STDOUT,
            env=env, creationflags=_spawn_flags(_CREATE_NEW_PROCESS_GROUP),
        )
    PID_FILE.write_text(str(p.pid))
    # 外部启动次数 +1（持久化，含手动/定时/重启）
    try:
        n = load_json_file(LAUNCH_FILE, {"count": 0}).get("count", 0) + 1
        LAUNCH_FILE.write_text(json.dumps({"count": n}), encoding="utf-8")
    except Exception:
        pass
    try:
        import time as _t
        START_FILE.write_text(str(_t.time()))
    except Exception:
        pass
    return f"已启动 (PID {p.pid})"


def launch_count():
    """外部启动次数"""
    return load_json_file(LAUNCH_FILE, {"count": 0}).get("count", 0)


def run_status():
    """签到进程写入的运行状态（查询/重启等）"""
    return load_json_file(RUN_STATUS_FILE, {})


def stop():
    pid = find_running()
    if not pid:
        return "进程未在运行"
    # /T 连同一进程树的子进程一起杀（单文件 exe 有引导进程 + 真正监控子进程，
    # 不加 /T 会漏掉监控子进程，导致“停止不了、一直监控”）
    r = _run(["taskkill", "/PID", str(pid), "/T", "/F"])
    if PID_FILE.exists():
        PID_FILE.unlink()
    if START_FILE.exists():
        START_FILE.unlink()
    return f"已停止 (PID {pid})"


# ============================================================
# 定时启动 / 定时关闭（基于 Windows 任务计划，重启后仍生效）
# ============================================================

def _norm_hhmm(s):
    """校验并规范化时间为 HH:MM（24小时制），不合法返回 None"""
    m = re.match(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$", s or "")
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _minutes(hhmm):
    """HH:MM → 当日分钟数；非法返回 None"""
    t = _norm_hhmm(hhmm)
    if not t:
        return None
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# 时段重复方式（0=周一 … 6=周日）
DAY_PRESETS = {
    "daily": [0, 1, 2, 3, 4, 5, 6],
    "weekday": [0, 1, 2, 3, 4],
    "weekend": [5, 6],
}
DAY_LABELS = {"daily": "每天", "weekday": "工作日", "weekend": "周末"}


def _slot_days(slot):
    """时段生效的星期列表（默认每天）"""
    key = str((slot or {}).get("days") or "daily").strip().lower()
    return list(DAY_PRESETS.get(key, DAY_PRESETS["daily"]))


def schedule_load():
    """读取定时设置 {enabled, slots:[{start,stop,days},...]}，没有返回空字典。
    自动把旧版单时段 {start,stop} 迁移为 slots。"""
    try:
        d = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8-sig"))
        if not isinstance(d, dict):
            return {}
        slots = d.get("slots") or []
        # 兼容旧格式：没有 slots 但有 start/stop 时转成一个时段
        if not slots and (d.get("start") or d.get("stop")):
            slots = [{"start": d.get("start"), "stop": d.get("stop")}]
        fixed = []
        for s in slots:
            if not isinstance(s, dict):
                continue
            days = str(s.get("days") or "daily").strip().lower()
            fixed.append({"start": s.get("start"), "stop": s.get("stop"),
                          "days": days if days in DAY_PRESETS else "daily"})
        d["slots"] = fixed
        return d
    except Exception:
        return {}


def schedule_window_active(slot, when=None):
    """现在是否落在该时段内（支持跨天，如 22:00~02:00）。

    - 只有 start：从 start 起一直有效；只有 stop：到 stop 为止有效。
    - days 决定星期几生效；跨天时段按"前一天开始"判断。
    """
    when = when or datetime.now()
    st = _minutes((slot or {}).get("start"))
    en = _minutes((slot or {}).get("stop"))
    days = set(_slot_days(slot))
    cur = when.hour * 60 + when.minute
    today = when.weekday()
    if st is None and en is None:
        return False
    if st is not None and en is not None:
        if st == en:
            return False
        if st < en:
            return today in days and st <= cur < en
        if today in days and cur >= st:
            return True
        prev = (today - 1) % 7
        return prev in days and cur < en
    if st is not None:
        return today in days and cur >= st
    return today in days and cur < en


def schedule_active_now(when=None):
    """当前是否处于定时运行窗口内。

    未启用定时（或没有任何时段）→ True，表示不限制，保持 7×24 监控。
    """
    d = schedule_load()
    if not d.get("enabled"):
        return True
    slots = [s for s in (d.get("slots") or [])
             if s.get("start") or s.get("stop")]
    if not slots:
        return True
    return any(schedule_window_active(s, when) for s in slots)


def schedule_next_change(when=None):
    """下一次时段边界 → (datetime, 'start'|'stop')；没有定时返回 (None, None)"""
    d = schedule_load()
    if not d.get("enabled"):
        return None, None
    when = when or datetime.now()
    best, kind = None, None
    for s in d.get("slots") or []:
        st = _minutes(s.get("start"))
        en = _minutes(s.get("stop"))
        days = set(_slot_days(s))
        for offset in range(0, 8):
            day = (when.weekday() + offset) % 7
            if day not in days:
                continue
            base = (when + timedelta(days=offset)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            for mins, k in ((st, "start"), (en, "stop")):
                if mins is None:
                    continue
                cand = base + timedelta(minutes=mins)
                if cand <= when:
                    continue
                if best is None or cand < best:
                    best, kind = cand, k
    return best, kind


def schedule_status_text(when=None):
    """界面/控制台用的一行定时状态：是否在时段内 + 下次切换"""
    when = when or datetime.now()
    d = schedule_load()
    if not d.get("enabled") or not d.get("slots"):
        return "未设置定时（7×24 监控）"
    body = "，".join(
        f"{DAY_LABELS.get(str(s.get('days') or 'daily'), '每天')} "
        f"{s.get('start') or '-'}~{s.get('stop') or '-'}"
        for s in d["slots"])
    nxt, kind = schedule_next_change(when)
    inside = schedule_active_now(when)
    if nxt is None:
        return f"{body}（当前{'在时段内' if inside else '不在时段内'}）"
    tip = f"下次 {nxt.strftime('%m-%d %H:%M')} " + (
        "自动启动" if kind == "start" else "自动停止")
    return f"{body}（当前{'在时段内' if inside else '不在时段内'}，{tip}）"


def _task_command(action):
    """任务动作：打包后调用 exe 自身；脚本模式调用 pythonw + control_panel.py"""
    if FROZEN:
        return str(sys.executable), action
    return str(PYTHONW), f'"{BASE / "control_panel.py"}" {action}'


def _task_xml(action, hhmm, days_key, description):
    """构造任务 XML。

    schtasks /Create 默认模板是 DisallowStartIfOnBatteries=true /
    StopIfGoingOnBatteries=true / StartWhenAvailable=false：笔记本一用电池就
    不触发、错过时间也不补跑，定时形同虚设。这里直接用 XML：
    允许电池、错过后补跑、忽略重复实例、不设执行时长上限。
    """
    from xml.sax.saxutils import escape as _esc

    exe, args = _task_command(action)
    days = sorted(set(_slot_days({"days": days_key})))
    if len(days) == 7:
        sched = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]
        dow = "".join(f"<{names[d]}/>" for d in days)
        sched = ("<ScheduleByWeek><WeeksInterval>1</WeeksInterval>"
                 f"<DaysOfWeek>{dow}</DaysOfWeek></ScheduleByWeek>")
    start_date = datetime.now().strftime("%Y-%m-%d")
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{_esc(description)}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_date}T{hhmm}:00</StartBoundary>
      <Enabled>true</Enabled>
      {sched}
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_esc(exe)}</Command>
      <Arguments>{_esc(args)}</Arguments>
      <WorkingDirectory>{_esc(str(BASE))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''


def _create_task(task_name, action, hhmm, days_key="daily"):
    """按 XML 注册任务（schtasks /XML 要求 UTF-16 编码的文件）"""
    xml = _task_xml(action, hhmm, days_key,
                    f"XMU 畅课签到 - {DAY_LABELS.get(days_key, '每天')} "
                    f"{hhmm} {action}")
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="xmu_task_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml.encode("utf-16"))
        r = _run(["schtasks", "/Create", "/TN", task_name, "/XML", path, "/F"])
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def _list_ac_tasks():
    """列出系统中所有 XMU-AC 定时任务名（含多时段带编号与旧的无编号任务）"""
    names = []
    try:
        r = _run(["schtasks", "/Query", "/FO", "CSV", "/NH"])
        import csv as _csv, io as _io
        for row in _csv.reader(_io.StringIO(r.stdout)):
            if row and row[0] and "XMU-AC-Auto" in row[0]:
                names.append(row[0].lstrip("\\"))
    except Exception:
        pass
    return names


def _delete_all_tasks():
    """删除所有本程序的定时任务（每个时段一对，避免遗留）"""
    for name in _list_ac_tasks():
        _run(["schtasks", "/Delete", "/TN", name, "/F"])


def _delete_task(task_name):
    _run(["schtasks", "/Delete", "/TN", task_name, "/F"])


def schedule_apply(slots):
    """按多个时间段注册定时。slots: [{"start","stop","days"}, ...]

    返回结果消息；时间格式非法时明确报错且不改动现有设置。
    """
    cleaned, bad = [], []
    for i, s in enumerate(slots or [], start=1):
        if not isinstance(s, dict):
            continue
        raw_st = str(s.get("start") or "").strip()
        raw_en = str(s.get("stop") or "").strip()
        if not raw_st and not raw_en:
            continue
        st = _norm_hhmm(raw_st) if raw_st else None
        en = _norm_hhmm(raw_en) if raw_en else None
        if raw_st and not st:
            bad.append(f"时段{i} 启动时间 “{raw_st}” 应为 HH:MM")
        if raw_en and not en:
            bad.append(f"时段{i} 关闭时间 “{raw_en}” 应为 HH:MM")
        if not st and not en:
            continue
        days = str(s.get("days") or "daily").strip().lower()
        if days not in DAY_PRESETS:
            days = "daily"
        cleaned.append({"start": st, "stop": en, "days": days})
    if bad:
        return "时间格式不对，未做任何更改：\n" + "\n".join(bad)
    if not cleaned:
        return "没有有效时间段（时间格式 HH:MM，如 07:30），未做更改"

    # 先清掉旧任务，再按编号逐个注册
    _delete_all_tasks()
    msgs = []
    for i, s in enumerate(cleaned, start=1):
        label = DAY_LABELS.get(s["days"], "每天")
        if s["start"]:
            ok, _out = _create_task(f"{TASK_START}-{i}", "start", s["start"],
                                    s["days"])
            msgs.append(f"时段{i}（{label}）启动 {s['start']}: "
                        + ("已设置" if ok else "失败"))
        if s["stop"]:
            ok, _out = _create_task(f"{TASK_STOP}-{i}", "stop", s["stop"],
                                    s["days"])
            msgs.append(f"时段{i}（{label}）关闭 {s['stop']}: "
                        + ("已设置" if ok else "失败"))

    SCHEDULE_FILE.write_text(
        json.dumps({"enabled": True, "slots": cleaned}, ensure_ascii=False,
                   indent=2),
        encoding="utf-8")
    msgs.append(f"当前：{schedule_status_text()}")
    return "\n".join(msgs)


def schedule_setup(start_t, stop_t):
    """兼容旧的单时段调用（命令行）"""
    return schedule_apply([{"start": start_t, "stop": stop_t}])


def schedule_cancel():
    """取消全部定时任务（所有时间段）"""
    _delete_all_tasks()
    data = schedule_load()
    data["enabled"] = False
    data["slots"] = []
    SCHEDULE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "已取消全部定时任务（恢复 7×24 监控）"


def schedule_line():
    """供主界面/控制台展示当前定时状态（多时段）"""
    d = schedule_load()
    if not d.get("enabled") or not d.get("slots"):
        return "定时: 未设置"
    parts = []
    for s in d["slots"]:
        label = DAY_LABELS.get(str(s.get("days") or "daily"), "每天")
        parts.append(f"{label} {s.get('start') or '-'}~{s.get('stop') or '-'}")
    return f"定时({len(parts)}段): " + "，".join(parts)


def schedule_dialog():
    """控制台版定时设置（多时段）"""
    _clear_screen()
    print("=" * 60)
    print("  定时设置（多时段，HH:MM；可输入多个，空行结束）")
    print("=" * 60)
    print(f"  当前：{schedule_line()}")
    print("  重复可选：每天 / 工作日 / 周末（直接回车=每天）")
    print("-" * 60)
    slots = []
    i = 1
    while True:
        s = input(f"  时段{i} 启动时间（回车结束）: ").strip()
        if not s:
            break
        e = input(f"  时段{i} 关闭时间: ").strip()
        d = input(f"  时段{i} 重复(每天/工作日/周末): ").strip()
        days = {"工作日": "weekday", "周末": "weekend"}.get(d, "daily")
        slots.append({"start": s, "stop": e, "days": days})
        i += 1
    print("-" * 60)
    print("  " + schedule_apply(slots).replace("\n", "\n  "))
    input("\n  按回车键返回...")


def tail_events(n=12):
    """从日志里挑出签到事件行（形如 [OK] xxx），取最近 n 条"""
    if not LOG_FILE.exists():
        return ["(尚无日志，启动后才会产生)"]
    try:
        raw = LOG_FILE.read_bytes()
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            lines = raw.decode("gbk", errors="replace").splitlines()
    except Exception:
        return ["(日志读取失败)"]
    events = []
    for raw in lines:
        line = ANSI_RE.sub("", raw).strip()
        # 事件行以 [ 开头；dashboard 整屏刷屏的行不匹配
        if line.startswith("[") and "监控中" not in line:
            events.append(line)
    if not events:
        return ["(运行中，暂无签到事件)"]
    return events[-n:]


def today_done():
    """今日成功签到，按课程汇总。返回 {课程: {n, types:{数字:次数}, last:'HH:MM:SS'}}"""
    if not RECORD_FILE.exists():
        return {}
    try:
        recs = json.loads(RECORD_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    today = datetime.now().strftime("%Y-%m-%d")
    by = {}
    for r in recs:
        if not str(r.get("time", "")).startswith(today):
            continue
        c = r.get("course") or "?"
        e = by.setdefault(c, {"types": {}, "n": 0, "last": ""})
        t = r.get("type") or "?"
        e["types"][t] = e["types"].get(t, 0) + 1
        e["n"] += 1
        e["last"] = str(r.get("time", ""))[11:]
    return by


def status_line():
    pid = find_running()
    if pid:
        pid_file_mark = "（本面板启动）" if PID_FILE.exists() else ""
        return f"[运行中] PID {pid} {pid_file_mark}"
    return "[未运行]"


def show():
    _clear_screen()
    W = 60
    print("=" * W)
    print("  XMU 畅课全自动签到 - 控制台".center(W - 4))
    print("=" * W)
    print(f"  状态: {status_line()}   时间: {now()}")
    print(f"  {schedule_line()}")
    print("-" * W)
    today = today_done()
    if today:
        n = sum(e["n"] for e in today.values())
        print(f"  今日已签到 {n} 次:")
        for c in sorted(today):
            e = today[c]
            types = "  ".join(f"{k}×{v}" for k, v in e["types"].items())
            print(f"    ✓ {c}  ({types})  最近 {e['last']}")
    else:
        print("  今日已签到: (暂无，监控中...)")
    print("-" * W)
    for e in tail_events(6):
        print("  · " + e)
    print("-" * W)
    print("  [1] 启动/重启    [2] 停止    [3] 定时设置")
    print("  [4] 取消定时     [0] 退出   (5秒自动刷新)")
    print("=" * W)


def wait_key(timeout=5.0):
    """等一个按键；超时返回 None（用于自动刷新）。"""
    end = time.time() + timeout
    while time.time() < end:
        if msvcrt.kbhit():
            return msvcrt.getwch()
        time.sleep(0.2)
    return None


def main():
    while True:
        show()
        c = wait_key()
        if c is None:
            continue            # 超时无按键 → 刷新
        if c == "1":
            print(">>", start())
            time.sleep(1.2)
        elif c == "2":
            print(">>", stop())
            time.sleep(1.2)
        elif c == "3":
            schedule_dialog()
        elif c == "4":
            print(">>", schedule_cancel())
            time.sleep(1.2)
        elif c == "0":
            break


def run_cli():
    """命令行模式：供任务计划/脚本静默调用 start/stop/status。返回是否已处理。"""
    if len(sys.argv) < 2:
        return False
    cmd = sys.argv[1].strip().lower()
    if cmd == "start":
        print(start())
    elif cmd == "stop":
        print(stop())
    elif cmd == "status":
        print(status_line())
    else:
        print(f"未知命令: {cmd}（支持 start / stop / status）")
    return True


if __name__ == "__main__":
    if not run_cli():
        main()
