"""
XMU Tronclass 全自动签到脚本
- xmulogin 纯 API CAS 登录（无浏览器，稳定）
- Cookie 持久化（重启免登录）
- 数字签到：API 直接抓码 → 提交
- GPS 签到：两条线三角定位自动算坐标 → 提交
  提交后按名单里本人那条 rollcall_status 确认是否真签上（不是看点名状态）
- 签到门控：等班里已签超过 1/4 或超过 20 人再签（避免第一个签）
  已签人数按名单每人的 rollcall_status 统计
- 内置看门狗：崩溃自动重启

接口口径备忘（两个 status 不是一回事）:
- GET /api/course/{id}/student/{sid}/rollcalls 的 status 是「本人在该次点名里
  的状态」：未签 = absent，签上 = on_call_fine；列表不返回 end_time
- GET /api/rollcall/{id}/student_rollcalls 顶层的 status 是「点名本身的状态」：
  in_progress / finished 等；数字码和结束时间从这里取

用法:
  $env:STUDENT_ID="你的学号"
  $env:PASSWORD="你的密码"
  python auto_checkin.py
"""
import getpass
import json
import math
import os
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from xmulogin import xmulogin


class _NoProxySession(requests.Session):
    """统一忽略系统/环境代理的会话。

    部署机是校园网直连；曾出现过残留的失效系统代理（127.0.0.1:5301，
    无监听）把所有请求拦成 ProxyError，重启后连登录都会失败。
    xmulogin 内部同样用 requests.Session()，替换类定义一并覆盖。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trust_env = False
        self.proxies = {}


requests.Session = _NoProxySession

# ============================================================
# 配置 —— 优先级：环境变量 > 凭据文件 creds.env > 交互输入
# 首次交互输入后会自动保存到 creds.env，以后启动无需再输入。
# ============================================================
# 打包成 exe 后数据文件放 exe 同目录；脚本模式放脚本目录
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CRED_FILE = APP_DIR / "creds.env"
COURSE_FILE = APP_DIR / "course_filter.json"
INTERVAL = 15           # 轮询间隔秒数
# GPS 坐标通过三角定位自动获取，无需手动填写

# ---- 签到门控：等班里先签的人多了再签，避免总是第一个签 ----
CHECKIN_GATE = True          # 总开关；False = 发现即签（原行为）
CHECKIN_RATIO = 0.25         # 已签人数 > 全班人数 × 此比例（1/4）才签
CHECKIN_MIN_SIGNED = 20      # 已签人数 > 此值也直接签
CHECKIN_MAX_WAIT = 120       # 等待超过此秒数直接签（保底）
CHECKIN_MIN_REMAINING = 30   # 距签到结束不足此秒数立即签（保底）
# ============================================================

def _load_cred_file():
    """读取 creds.env，返回 {STUDENT_ID: .., PASSWORD: ..}（读不到返回空字典）"""
    creds = {}
    try:
        if CRED_FILE.exists():
            for ln in CRED_FILE.read_text(encoding="utf-8-sig").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    creds[k.strip()] = v.strip()
    except Exception as e:
        print(f"[配置] 读取 {CRED_FILE.name} 失败: {e}")
    return creds


def _save_cred_file(student_id, password):
    """把学号/密码保存到 creds.env，下次启动自动读取（明文存储，仅本机使用）"""
    try:
        CRED_FILE.write_text(
            f"STUDENT_ID={student_id}\nPASSWORD={password}\n",
            encoding="utf-8",
        )
        print(f"[配置] 账号已保存到 {CRED_FILE.name}，下次启动无需再输入")
    except Exception as e:
        print(f"[配置] 保存账号失败: {e}")


def load_disabled_courses():
    """读取 course_filter.json 里"不监控"的课程 id 集合。

    界面上的课程勾选就写在这个文件里（只记未勾选的例外，新课程默认监控）。
    每轮轮询都重新读一次 → 界面上改完勾选立即生效，不用重启监控进程。
    """
    try:
        if COURSE_FILE.exists():
            d = json.loads(COURSE_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(d, dict):
                return {str(x) for x in (d.get("disabled") or [])}
    except Exception as e:
        print(f"[配置] 读取 {COURSE_FILE.name} 失败: {e}，本次按全部课程监控")
    return set()


_file_creds = _load_cred_file()
STUDENT_ID = os.environ.get("STUDENT_ID") or _file_creds.get("STUDENT_ID")
PASSWORD = os.environ.get("PASSWORD") or _file_creds.get("PASSWORD")

if not STUDENT_ID:
    STUDENT_ID = input("学号: ").strip()
if not PASSWORD:
    PASSWORD = getpass.getpass("密码: ")

if not STUDENT_ID or not PASSWORD:
    print("错误: 学号和密码不能为空")
    exit(1)

# 凭据此前不在文件里（本次靠交互输入得到）→ 落盘，实现以后免输入
if not _file_creds.get("STUDENT_ID") or not _file_creds.get("PASSWORD"):
    _save_cred_file(STUDENT_ID, PASSWORD)

BASE_URL = "https://lnt.xmu.edu.cn"
SESSION_FILE = APP_DIR / "auto_checkin_session.json"
RECORD_FILE = APP_DIR / "checkin_record.json"
STATUS_FILE = APP_DIR / "auto_checkin_status.json"


def write_run_status(restart_count=None, course_queries=None, query_total=None,
                     events=None):
    """把运行状态（看门狗重启、每门课查询、总查询、最近事件）落盘供界面读取"""
    try:
        data = {}
        if STATUS_FILE.exists():
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8-sig"))
        if restart_count is not None:
            data["restart_count"] = restart_count
        if course_queries is not None:
            data["course_queries"] = course_queries
        if query_total is not None:
            data["query_total"] = query_total
        if events is not None:
            data["events"] = events[-40:]
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STATUS_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

HEADERS_BASE = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

# GPS 三角定位误差容忍（米）：两圆交点距离在此范围内视为相交
GPS_DISTANCE_TOLERANCE = 5.0
# 未知状态最大重试次数（超出后视为终端，不再重试）
MAX_UNKNOWN_RETRIES = 10

# 数字码诊断：rid → 最近一次 students-detail 原始响应；拿不到码时落盘一次
LAST_DETAIL_RAW = {}
_CODE_MISSING_DUMPED = set()
# 数字码备用通道（课表日期接口）缓存与诊断
TIMETABLE_CODE_CACHE = {}
LAST_TIMETABLE_RAW = {}


# ============================================================
# 登录模块：xmulogin + cookie 持久化
# ============================================================

def record_ok(rtype, course, detail):
    """追加一条成功签到记录（供控制台实时展示今日已签到课程）"""
    try:
        recs = []
        if RECORD_FILE.exists():
            recs = json.loads(RECORD_FILE.read_text(encoding="utf-8-sig"))
        recs.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": rtype, "course": course, "detail": detail,
        })
        RECORD_FILE.write_text(json.dumps(recs[-200:], ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _save_cookies(session):
    """保存完整 cookie jar 到文件（含 domain/path/expires/secure 等属性）"""
    try:
        cookies = []
        for cookie in session.cookies:
            cookies.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
                "secure": cookie.secure,
                "rest": cookie._rest,
            })
        with open(SESSION_FILE, "w") as f:
            json.dump(cookies, f)
        print(f"[登录] Cookie 已保存到 {SESSION_FILE}")
    except Exception as e:
        print(f"[登录] 保存 Cookie 失败: {e}")


def _load_cookies(session):
    """从文件加载完整 cookie jar"""
    if not SESSION_FILE.exists():
        return False
    try:
        with open(SESSION_FILE, "r") as f:
            cookies = json.load(f)
        for c in cookies:
            session.cookies.set(
                name=c["name"],
                value=c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
                # 忽略保存时的过期时间戳：cookie 是否有效由服务端判定。
                # 会话运行中会被服务端滑动续期，而磁盘快照里的 expires
                # 停留在保存时刻，重启载入时会被客户端提前丢弃（401）。
                expires=None,
                secure=c.get("secure", False),
                rest=c.get("rest", {}),
            )
        return True
    except Exception as e:
        print(f"[登录] 加载 Cookie 失败: {e}")
        return False


def do_login():
    """xmulogin 纯 API CAS 登录，返回 (requests.Session, student_id)"""
    session = requests.Session()
    session.headers.update(HEADERS_BASE)

    # 尝试加载已保存的 session
    if _load_cookies(session):
        try:
            r = session.get(f"{BASE_URL}/api/profile", timeout=10)
            if r.status_code == 200 and r.json().get("id"):
                sid = r.json()["id"]
                print(f"[登录] Cookie 有效, student_id={sid}")
                return session, sid
            print("[登录] Cookie 已过期，重新登录...")
        except Exception as e:
            print(f"[登录] Cookie 校验请求失败: {e}，重新登录...")

    # xmulogin 登录（type=3 为 Tronclass）
    print("[登录] xmulogin CAS 登录中...")
    session = xmulogin(type=3, username=STUDENT_ID, password=PASSWORD)
    if not session:
        print("[登录] 登录失败")
        return None, None

    # 获取 student_id
    r = session.get(f"{BASE_URL}/api/profile", timeout=10)
    if r.status_code != 200:
        print("[登录] 获取用户信息失败")
        return None, None
    student_id = r.json()["id"]
    print(f"[登录] 登录成功, student_id={student_id}")

    _save_cookies(session)
    return session, student_id


# ============================================================
# API 工具函数（全部接收 requests.Session）
# ============================================================

def _get_semester(session):
    try:
        r = session.get(f"{BASE_URL}/api/current-semester-info", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return str(data["semester"]["id"]), str(data["academic_year"]["id"])
    except Exception as e:
        print(f"[!] 获取学期信息失败: {e}，使用默认值")
    return "29", "12"


def get_courses(session):
    s_id, y_id = _get_semester(session)
    payload = {
        "conditions": {
            "semester_id": [s_id], "academic_year_id": [y_id],
            "keyword": "", "classify_type": "recently_started",
            "display_studio_list": False,
        },
        "fields": "id,name,display_name",
        "page": 1, "page_size": 30,
        "showScorePassedStatus": False,
    }
    r = session.post(f"{BASE_URL}/api/my-courses", json=payload, timeout=10)
    try:
        data = r.json()
    except Exception:
        return []
    if isinstance(data, list):
        courses = data
    elif "courses" in data:
        courses = data["courses"]
    elif "data" in data:
        courses = data["data"]
    else:
        return []
    seen_cids, unique = set(), []
    for c in courses:
        cid = c.get("id")
        if cid not in seen_cids:
            seen_cids.add(cid)
            unique.append(c)
    return unique


def get_rollcalls(course_id, session, student_id):
    url = f"{BASE_URL}/api/course/{course_id}/student/{student_id}/rollcalls?page=1&page_size=20"
    r = session.get(url, timeout=10)
    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("rollcalls", data.get("data", []))
    return []


def answer_number(rollcall_id, code, session):
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    payload = {"deviceId": str(uuid.uuid4()), "numberCode": str(code)}
    try:
        r = session.put(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  [X] 提交签到失败: {e}")
        return False


def get_rollcall_detail(rollcall_id, session):
    """读点名详情接口，返回 (number_code, 点名级 status, end_time) 或 (None, None, None)。

    ⚠️ 顶层 status 是「点名本身」的状态（in_progress / finished…），不是本人签没签上；
    数字码和结束时间从这里取，本人的状态看课程列表或名单。
    """
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/student_rollcalls"
    try:
        r = session.get(url, timeout=10)
        data = r.json()
        if isinstance(data, dict):
            # 留一份原始响应（剔除名单大数组）供取码失败时诊断
            LAST_DETAIL_RAW[rollcall_id] = {
                k: v for k, v in data.items() if k != "student_rollcalls"
            }
            LAST_DETAIL_RAW[rollcall_id]["_http_status"] = r.status_code
            LAST_DETAIL_RAW[rollcall_id]["_n_students"] = len(
                data.get("student_rollcalls") or [])
        return data.get("number_code"), data.get("status"), data.get("end_time")
    except Exception as e:
        print(f"  [X] 读取点名详情失败: {e}")
        return None, None, None


def get_number_code_from_timetable(rollcall_id, course_id, rollcall_time, session):
    """数字码备用通道：GET /api/timetable_rollcalls?course_ids=..&rollcall_date=..

    2026-10-10 起 /api/rollcall/{id}/student_rollcalls 不再返回 number_code，
    课程页改用这个日期接口（前端 getTimeTableRollcalls）。rollcall_date 取
    rollcall_time 的 UTC 日期（已用多场历史点名回验）。返回码字符串或 None。
    """
    cached = TIMETABLE_CODE_CACHE.get(rollcall_id)
    if cached:
        return cached
    try:
        dt = datetime.fromisoformat(str(rollcall_time).replace("Z", "+00:00"))
        date_str = dt.astimezone(timezone.utc).date().isoformat()
    except Exception:
        date_str = datetime.now(timezone.utc).date().isoformat()
    try:
        r = session.get(
            f"{BASE_URL}/api/timetable_rollcalls",
            params=[("course_ids", str(course_id)), ("rollcall_date", date_str)],
            timeout=10,
        )
        if r.status_code != 200:
            return None
        items = r.json().get("rollcalls") or []
        LAST_TIMETABLE_RAW[rollcall_id] = {
            "queried_course_id": str(course_id),
            "queried_date": date_str,
            "http_status": r.status_code,
            "n_rollcalls": len(items),
            "r_ids": [it.get("id") for it in items if isinstance(it, dict)][:20],
        }
        for item in items:
            if not isinstance(item, dict) or str(item.get("id")) != str(rollcall_id):
                continue
            code = item.get("number_code")
            if code:
                TIMETABLE_CODE_CACHE[rollcall_id] = str(code)
                return str(code)
    except Exception:
        pass
    return None


def _find_me(students):
    """在名单里找本人那条记录，找不到返回 None"""
    return next((s for s in students if isinstance(s, dict)
                 and s.get("user_no") is not None
                 and str(s["user_no"]) == str(STUDENT_ID)), None)


def check_my_checkin_status(rollcall_id, session, retries=3, delay=0.4):
    """回查「本人」在本次签到里的状态，返回名单里的 rollcall_status。

    ⚠️ 签到成没成功只能看本人那条的 rollcall_status（签上后为 on_call_fine），
    详情接口顶层的 status 是点名本身的状态，拿它判成功会把已签上的 GPS 签到
    报成失败。提交后服务端落库有延迟，带几次重读。
    """
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/student_rollcalls"
    status = None
    for i in range(retries):
        try:
            r = session.get(url, timeout=10)
            if r.status_code != 200:
                return None
            students = r.json().get("student_rollcalls")
            if not isinstance(students, list):
                return None
            me = _find_me(students)
            if me is None:
                print("  [!] 名单里没找到本人记录，无法确认签到结果")
                return None
            status = me.get("rollcall_status")
            if status == SIGNED_STUDENT_STATUS:
                return status
        except Exception as e:
            print(f"  [X] 回查本人状态失败: {e}")
            return None
        if i < retries - 1:
            time.sleep(delay)
    return status


def get_signed_stats(rollcall_id, session):
    """查询签到进度，返回 (signed, total, end_time) 或 (None, None, None)。

    已签人数按名单每人的 rollcall_status 统计（签上后变 on_call_fine）；
    名单总人数即本班应到人数。不能用 updated_at 判断：本校名单里缺勤学生
    也从建记录起就带 updated_at。自校：若判据把还没签的自己也算成已签，
    说明与接口口径不符，按拿不到人数处理，宁可立即签也不卡住。
    """
    try:
        r = session.get(
            f"{BASE_URL}/api/rollcall/{rollcall_id}/student_rollcalls",
            timeout=10,
        )
        if r.status_code != 200:
            return None, None, None
        data = r.json()
        students = data.get("student_rollcalls") if isinstance(data, dict) else None
        if not isinstance(students, list):
            return None, None, None
        students = [s for s in students if isinstance(s, dict)]
        if not students:
            return None, None, None
        me = _find_me(students)
        if me is not None and me.get("rollcall_status") == SIGNED_STUDENT_STATUS:
            print("  [!] 本人已签上却仍在门控流程，直接放行")
            return None, None, None
        signed = sum(
            1 for s in students if s.get("rollcall_status") == SIGNED_STUDENT_STATUS
        )
        return signed, len(students), data.get("end_time")
    except Exception as e:
        print(f"  [X] 查询签到人数失败: {e}")
        return None, None, None


def checkin_gate(rollcall_id, session, waited_sec):
    """签到门控：判断是否应等班里更多人先签。

    开签条件（任一满足）：已签 > 全班 1/4，或已签 > CHECKIN_MIN_SIGNED。
    保底（漏签比早签更糟，以下情况直接放行）：查不到人数 / 等待超过
    CHECKIN_MAX_WAIT 秒 / 距签到结束不足 CHECKIN_MIN_REMAINING 秒。
    返回 (need_wait, msg)。
    """
    if not CHECKIN_GATE:
        return False, ""
    signed, total, end_time = get_signed_stats(rollcall_id, session)
    if signed is None:
        return False, "查不到签到人数，直接签"
    msg = f"已签 {signed}/{total}"
    if signed / total > CHECKIN_RATIO or signed > CHECKIN_MIN_SIGNED:
        return False, f"{msg}，达标开签"
    remaining = _time_remaining(end_time)
    if waited_sec >= CHECKIN_MAX_WAIT:
        return False, f"{msg}，等待超 {CHECKIN_MAX_WAIT}s，直接签"
    if remaining is not None and remaining <= CHECKIN_MIN_REMAINING:
        return False, f"{msg}，剩余 {int(remaining)}s，直接签"
    return True, f"{msg}，未达标继续等"


# ============================================================
# GPS 三角定位（两条线交点反推目标坐标）
# ============================================================

PROBE_LAT1, PROBE_LON1 = 24.3, 118.0
PROBE_LAT2, PROBE_LON2 = 24.6, 118.2


def _latlon_to_xy(lat, lon, lat0, lon0):
    R = 6371000.0
    x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * R
    return x, y


def _xy_to_latlon(x, y, lat0, lon0):
    R = 6371000.0
    lat = lat0 + math.degrees(y / R)
    lon = lon0 + math.degrees(x / (R * math.cos(math.radians(lat0))))
    return lat, lon


def _circle_intersections(x1, y1, d1, x2, y2, d2):
    """两圆交点。返回 ((x1,y1), (x2,y2)) 或 None。

    容忍 GPS 距离舍入误差：两圆若在 tolerance 米内不相交，
    尝试微调 d1/d2 使其相切。
    """
    x1, y1, d1 = float(x1), float(y1), float(d1)
    x2, y2, d2 = float(x2), float(y2), float(d2)
    D = math.hypot(x2 - x1, y2 - y1)

    if D < 1e-9:
        # 同心圆：无法确定方向
        return None

    if D > d1 + d2:
        # 两圆分离，尝试 GPS 误差容忍
        gap = D - (d1 + d2)
        if gap <= GPS_DISTANCE_TOLERANCE:
            # 将两圆半径各扩大 gap/2，使其相切
            adjust = gap / 2.0 + 0.1
            d1 += adjust
            d2 += adjust
        else:
            return None

    if D < abs(d1 - d2):
        # 一圆包含另一圆，尝试容忍
        gap = abs(d1 - d2) - D
        if gap <= GPS_DISTANCE_TOLERANCE:
            # 缩小大圆或扩大小圆使其相切
            if d1 > d2:
                d1 = D + d2 - 0.1
            else:
                d2 = D + d1 - 0.1
        else:
            return None

    a = (d1 ** 2 - d2 ** 2 + D ** 2) / (2 * D)
    h_sq = max(0, d1 ** 2 - a ** 2)
    h = math.sqrt(h_sq)
    xm = x1 + a * (x2 - x1) / D
    ym = y1 + a * (y2 - y1) / D
    rx = -(y2 - y1) * (h / D)
    ry = (x2 - x1) * (h / D)
    return (xm + rx, ym + ry), (xm - rx, ym - ry)


def _solve_target(lat1, lon1, lat2, lon2, d1, d2):
    lat0 = (lat1 + lat2) / 2
    lon0 = (lon1 + lon2) / 2
    x1, y1 = _latlon_to_xy(lat1, lon1, lat0, lon0)
    x2, y2 = _latlon_to_xy(lat2, lon2, lat0, lon0)
    sols = _circle_intersections(x1, y1, d1, x2, y2, d2)
    if sols is None:
        return None
    p1 = _xy_to_latlon(sols[0][0], sols[0][1], lat0, lon0)
    p2 = _xy_to_latlon(sols[1][0], sols[1][1], lat0, lon0)
    return p1, p2


def answer_radar_auto(rollcall_id, session):
    """GPS 签到：两条线三角定位，自动算目标坐标。

    返回 (success, lat, lon)。提交后回查名单里本人的 rollcall_status 确认
    签上（详情顶层 status 是点名本身的状态，不能拿来判成功）。
    """
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer"

    def _payload(lat, lon):
        return {
            "accuracy": 35, "altitude": 0, "altitudeAccuracy": None,
            "deviceId": str(uuid.uuid4()), "heading": None,
            "latitude": str(lat), "longitude": str(lon), "speed": None,
        }

    try:
        r1 = session.put(url, json=_payload(PROBE_LAT1, PROBE_LON1), timeout=10)
        r2 = session.put(url, json=_payload(PROBE_LAT2, PROBE_LON2), timeout=10)
    except Exception as e:
        print(f"  探针请求失败: {e}")
        return False, None, None

    # 探针直接命中（本次点名不限制范围，或探针坐标已在范围内）
    hit = None
    for r, lat, lon in [(r1, PROBE_LAT1, PROBE_LON1), (r2, PROBE_LAT2, PROBE_LON2)]:
        if r.status_code == 200:
            hit = (lat, lon)
    if hit is not None and check_my_checkin_status(rollcall_id, session) == SIGNED_STUDENT_STATUS:
        print(f"  探针命中，本人状态已确认: {SIGNED_STUDENT_STATUS}")
        return True, hit[0], hit[1]

    try:
        d1 = r1.json().get("distance")
        d2 = r2.json().get("distance")
    except Exception:
        print("  服务器未返回距离信息")
        return False, None, None

    if d1 is None or d2 is None:
        print(f"  未获取到距离: d1={d1}, d2={d2}")
        return False, None, None

    print(f"  探针距离: d1={d1}m, d2={d2}m")

    sols = _solve_target(PROBE_LAT1, PROBE_LON1, PROBE_LAT2, PROBE_LON2, d1, d2)
    if sols is None:
        print("  两圆无交点（已尝试误差容忍）")
        return False, None, None

    (lat_a, lon_a), (lat_b, lon_b) = sols

    for lat, lon in [(lat_a, lon_a), (lat_b, lon_b)]:
        try:
            r = session.put(url, json=_payload(lat, lon), timeout=10)
            if r.status_code == 200:
                status = check_my_checkin_status(rollcall_id, session)
                if status == SIGNED_STUDENT_STATUS:
                    print(f"  精确坐标命中，本人状态已确认: {status}")
                    return True, lat, lon
                print(f"  坐标提交 200 但本人状态为 {status}，尝试下一个...")
        except Exception as e:
            print(f"  候选坐标提交异常: {e}")

    print("  两个候选坐标均未确认成功")
    return False, None, None


# ============================================================
# 辅助
# ============================================================

# 判「某人已签到」的依据字段：名单里签上后 rollcall_status 变 on_call_fine
SIGNED_STUDENT_STATUS = "on_call_fine"

# 终端状态：签到成功、已结束、已过期、已关闭、已请假。
# absent = 课程列表里「本人在该次点名中尚未签」，点名进行中属正常值，不能当
# 未知状态刷屏，也不算终端。
TERMINAL_STATUSES = {"finished", "expired", "closed", "on_call_fine", "on_personal_leave"}

# 明确活跃的状态（可尝试签到）。课程列表的 absent / on_call_fine（本人状态）
# 与详情接口的 in_progress / finished（点名状态）都归进这两个集合，哪个接口
# 来的都能判「还要不要接着处理」。
ACTIVE_STATUSES = {
    "active", "on_going", "on_coming", "started", "in_progress",
    "waiting", "pending", "not_started", "absent",
}


def _time_remaining(end_time):
    """距 end_time 还剩多少秒；无 end_time 或解析失败返回 None"""
    if not end_time:
        return None
    try:
        dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None


def _end_time_passed(end_time):
    """检查 end_time 是否已过"""
    remaining = _time_remaining(end_time)
    return remaining is not None and remaining <= 0


def _is_terminal(status, end_time):
    """判断签到是否已终止（成功或过期），不应再重试。"""
    if status in TERMINAL_STATUSES:
        return True
    if status in ACTIVE_STATUSES:
        return _end_time_passed(end_time)
    # 未知 status + end_time 已过 → 视为终端
    if end_time and _end_time_passed(end_time):
        return True
    return False


# 点名本身已结束的状态值（列表项 rollcall_status / 详情接口顶层 status）
ROLLCALL_DEAD_STATUSES = {"finished", "closed", "expired", "ended",
                          "canceled", "cancelled"}
# 数字码兜底：点名时间过去这么久还没下发，基本不会再给了，直接放弃
CODE_GIVEUP_AGE = 6 * 3600


def _rollcall_age(rollcall_time):
    """点名时间距今多少秒（解析失败返回 None）"""
    if not rollcall_time:
        return None
    try:
        dt = datetime.fromisoformat(str(rollcall_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _rollcall_dead(rc, detail_status=None):
    """点名本身是否已结束 / 已过期。

    课程列表接口会把**已结束的点名长期留在列表里**：is_expired 仍是 false、
    本人 status 仍是 absent、详情 end_time 是 null。只看本人 status 就会把
    「昨天那场点名」当成还没签、可以签，于是每轮都重试、取不到码就刷屏
    （实测：一门课的事件刷了一整天）。这里用点名级状态兜住。
    """
    if rc.get("is_expired") is True:
        return True
    for flag in (rc.get("rollcall_status"), detail_status):
        if flag and str(flag).lower() in ROLLCALL_DEAD_STATUSES:
            return True
    return False


def _diag_has_rid(path, rid):
    """诊断文件里是否已经记录过这个 rid（跨重启去重，避免重复落盘）"""
    try:
        if not path.exists():
            return False
        txt = path.read_text(encoding="utf-8", errors="replace")
        return f'"rid": {rid},' in txt
    except Exception:
        return False


def _cap_diag_file(path, max_bytes=256 * 1024, keep_lines=40):
    """诊断文件超过上限时只保留最后若干条，避免无限增长"""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            lines = path.read_text(encoding="utf-8",
                                   errors="replace").splitlines()[-keep_lines:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def rollcall_label(rollcall):
    if rollcall.get("is_radar"):
        return "GPS"
    if rollcall.get("is_number"):
        return "数字"
    return "其他"


def _has_console():
    """本进程是否挂着真实控制台窗口（GUI / 无控制台 → False）"""
    if os.name != "nt":
        return sys.stdout is not None
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def clear():
    # 打包后（windowed 无控制台）执行 cls 会弹出 cmd 黑窗，必须跳过；
    # 脚本模式若由 pythonw/无控制台环境启动，同样跳过（否则黑窗一闪一闪）。
    if getattr(sys, "frozen", False) or not _has_console():
        return
    os.system("cls" if os.name == "nt" else "clear")


def _term_width():
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


def _display_width(text):
    """计算字符串在终端中的显示宽度（CJK 字符占 2 列）"""
    w = 0
    for ch in text:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def _cline(text):
    """居中打印（按显示宽度）"""
    w = _term_width()
    dw = _display_width(text)
    print(" " * max(0, (w - dw) // 2) + text)


def dashboard(courses, seen_count, query_count, start_time, events):
    # 打包后界面由 GUI 展示，这里不再刷屏（否则 checkin.log 每秒增长一整屏）
    if getattr(sys, "frozen", False):
        return
    clear()
    elapsed = int(time.time() - start_time)
    m, s = divmod(elapsed, 60)
    h, m = divmod(m, 60)
    t = time.strftime("%H:%M:%S")
    rt = f"{h}h{m}m{s}s" if h else f"{m}m{s}s"

    sep = "=" * 46

    for line in [
        "",
        "XMU Tronclass 全自动签到",
        sep,
        f"时间: {t}    运行: {rt}",
        f"课程: {len(courses)} 门    查询: {query_count} 次",
        "GPS : 自动三角定位",
        f"门控: 已签>{CHECKIN_RATIO:.0%} 或 >{CHECKIN_MIN_SIGNED} 人先签"
        if CHECKIN_GATE else "门控: 关闭",
        sep,
    ]:
        _cline(line)

    if events:
        for evt in events[-8:]:
            _cline(evt)
    else:
        _cline("(监控中...)")

    _cline(sep)
    _cline("Ctrl+C 退出")


# ============================================================
# 主循环
# ============================================================

def main_loop():
    # 1. 登录
    session, student_id = do_login()
    if not session or not student_id:
        print("[错误] 登录失败")
        return False

    # 2. 获取课程
    courses = get_courses(session)
    if not courses:
        print("[错误] 获取课程列表失败")
        return False
    course_names = {
        c["id"]: c.get("display_name") or c.get("name") or "?"
        for c in courses
    }
    print(f"[课程] 共 {len(courses)} 门，"
          f"按勾选监控 {len(courses) - len(load_disabled_courses())} 门")

    # 3. 监控循环
    seen = set()                # 已处理终端 / 已成功签到的 rid
    unknown_retries = {}        # rid → 未知状态重试次数
    gate_since = {}             # rid → 首次进入门控的时间戳（计算等待时长）
    last_wait_msg = {}          # rid → 上条 [WAIT] 消息（人数有变化才再报）
    announced = set()           # rid → 已播报过 [NEW]（不再重复播报）
    last_msg = {}               # rid → 上一条事件文本
    repeat_n = {}               # rid → 同一条消息已重复次数
    events = []
    query_count = 0
    course_queries = {}       # 课程名 → 该课程被查询次数
    start_time = time.time()
    last_poll = 0
    last_status_dump = 0

    def emit(rid, msg, every=4):
        """写事件，并对同一 rid 的重复消息做压缩：
        首次立即写；之后每 every 次才写一条并标注次数。
        否则一门卡住的课会每 15 秒刷一条，界面里全是它。"""
        if msg == last_msg.get(rid):
            n = repeat_n.get(rid, 1) + 1
            repeat_n[rid] = n
            if n % every:
                return
            events.append(f"{msg}（第 {n} 次）")
        else:
            last_msg[rid] = msg
            repeat_n[rid] = 1
            events.append(msg)

    dashboard(courses, len(seen), query_count, start_time, events)

    while True:
        now = time.time()

        # 每隔 INTERVAL 秒轮询一次课程
        if now - last_poll >= INTERVAL:
            dead_skipped = 0
            # 每轮重新读勾选：界面改完立即生效，不必重启监控
            disabled_courses = load_disabled_courses()
            for c in courses:
                cid = c["id"]
                name = course_names.get(cid, "?")
                if str(cid) in disabled_courses:
                    continue        # 没勾选的课完全不抓取
                try:
                    rollcalls = get_rollcalls(cid, session, student_id)
                except Exception as e:
                    print(f"[!] 获取 {name} 签到列表失败: {e}")
                    continue

                query_count += 1
                course_queries[name] = course_queries.get(name, 0) + 1

                for rc in rollcalls:
                    rid = rc.get("id") or rc.get("rollcall_id")
                    if not rid or rid in seen:
                        continue

                    # 本人状态一律看课程列表这条的 status（未签 = absent、
                    # 签上 = on_call_fine）；详情接口只用来取数字码和结束时间。
                    # ⚠️ 详情顶层 status 是点名级状态（in_progress / finished），
                    # 拿它判「还要不要接着处理」会把已签上的报成失败。
                    is_num = rc.get("is_number") and not rc.get("is_radar")
                    is_radar = bool(rc.get("is_radar"))
                    code = end_time = rc_status = None
                    if is_num or is_radar:
                        try:
                            code, rc_status, end_time = get_rollcall_detail(rid, session)[:3]
                        except Exception:
                            code = end_time = rc_status = None
                    status = rc.get("status") if (is_num or is_radar) else None

                    # 终端状态（已签上/已过期）→ 静默标记已处理
                    if _is_terminal(status, end_time):
                        seen.add(rid)
                        continue

                    # 点名本身已结束（列表长期保留昨天的点名）→ 直接丢掉，不再重试。
                    # 这是「界面里一直只有某一门课」的根因。
                    if _rollcall_dead(rc, rc_status):
                        seen.add(rid)
                        dead_skipped += 1
                        continue

                    # 数字签到：点名时间已过去很久还没下发码 → 服务端不会再给，
                    # 放弃，避免每轮都为空点名叫一次接口、刷一条事件
                    if is_num and not code:
                        age = _rollcall_age(rc.get("rollcall_time"))
                        if age is not None and age > CODE_GIVEUP_AGE:
                            seen.add(rid)
                            emit(rid, f"[GIVEUP] {name} 已过 {int(age // 3600)} 小时"
                                      f"仍未下发签到码，放弃")
                            continue

                    # 列表里连本人状态都没有（列表接口异常）→ 限次重试。
                    # 注意详情接口失败不影响该判断：数字码/结束时间下轮再取。
                    if status is None:
                        retries = unknown_retries.get(rid, 0)
                        if retries >= MAX_UNKNOWN_RETRIES:
                            seen.add(rid)
                            emit(rid,
                                 f"[GIVEUP] {name} 状态获取连续失败 {retries} 次，放弃")
                            unknown_retries.pop(rid, None)
                        else:
                            unknown_retries[rid] = retries + 1
                        continue

                    # 非标准 status（不在已知终端/活跃列表）→ 打印并继续尝试签到
                    if status not in TERMINAL_STATUSES and status not in ACTIVE_STATUSES:
                        emit(rid, f"[?] {name} 未知状态值: '{status}'，仍尝试签到")

                    # 可以尝试签到
                    rtype = rollcall_label(rc)

                    # 签到门控：等班里先签的人够多再签，避免总是第一个签。
                    # 等待期间 [NEW] 只报一次，[WAIT] 只在人数变化时报，防止刷屏。
                    if rc.get("is_number") or rc.get("is_radar"):
                        now_ts = time.time()
                        since = gate_since.setdefault(rid, now_ts)
                        if rid not in announced:
                            announced.add(rid)
                            emit(rid, f"[NEW] {name} ({rtype})")
                        need_wait, gate_msg = checkin_gate(rid, session, now_ts - since)
                        if need_wait:
                            if gate_msg != last_wait_msg.get(rid):
                                events.append(f"[WAIT] {name} {gate_msg}")
                                last_wait_msg[rid] = gate_msg
                            continue
                        gate_since.pop(rid, None)
                        last_wait_msg.pop(rid, None)
                        if gate_msg:
                            emit(rid, f"[GO] {name} {gate_msg}")
                    elif rid not in announced:
                        announced.add(rid)
                        emit(rid, f"[NEW] {name} ({rtype})")

                    if rc.get("is_number") and not rc.get("is_radar"):
                        if not code:
                            # 旧详情接口已不再稳定提供数字码，走课表日期接口备用通道
                            code = get_number_code_from_timetable(
                                rid, cid, rc.get("rollcall_time"), session)
                        if code:
                            ok = answer_number(rid, code, session)
                            if ok:
                                seen.add(rid)
                                record_ok("数字", name, f"码={code}")
                                events.append(f"[OK] 数字 {name} 码={code}")
                            else:
                                emit(rid, f"[FAIL] 数字 {name} 提交失败，将重试")
                        else:
                            emit(rid, f"[!] {name} 签到码暂未获取，将重试")
                            # 诊断取证：每个 rid 只落盘一次当时的原始详情响应
                            if rid not in _CODE_MISSING_DUMPED:
                                _CODE_MISSING_DUMPED.add(rid)
                                try:
                                    diag_path = SESSION_FILE.parent / "code_missing_diag.json"
                                    if not _diag_has_rid(diag_path, rid):
                                        dump = {
                                            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "course": name,
                                            "rid": rid,
                                            "list_item": {
                                                k: v for k, v in rc.items()
                                                if not isinstance(v, (list, dict))
                                            },
                                            "detail_raw": LAST_DETAIL_RAW.get(rid),
                                            "timetable_raw": LAST_TIMETABLE_RAW.get(rid),
                                        }
                                        # 诊断文件不能无限膨胀：超过 256KB 只保留最后 40 条
                                        _cap_diag_file(diag_path)
                                        with open(diag_path, "a", encoding="utf-8") as f:
                                            f.write(json.dumps(dump, ensure_ascii=False) + "\n")
                                except Exception:
                                    pass

                    elif rc.get("is_radar"):
                        ok, lat, lon = answer_radar_auto(rid, session)
                        if ok:
                            seen.add(rid)
                            record_ok("GPS", name, f"({lat:.4f}, {lon:.4f})")
                            events.append(f"[OK] GPS {name} ({lat:.4f}, {lon:.4f})")
                        else:
                            emit(rid, f"[FAIL] GPS {name} 将重试")

                    else:
                        emit(rid, f"[!] {name} 不支持的签到类型")
                        seen.add(rid)  # 不支持的类型，无法处理

            if dead_skipped:
                # 一次性汇总，避免每个历史点名各刷一条
                events.append(f"[SKIP] 已跳过 {dead_skipped} 个已结束的点名（不再重试）")

            last_poll = now
            # 每轮轮询写一次运行状态（界面读取）
            write_run_status(course_queries=course_queries, query_total=query_count,
                             events=events)

        # 限制 events 长度，防止内存泄漏
        if len(events) > 200:
            events = events[-100:]

        dashboard(courses, len(seen), query_count, start_time, events)
        time.sleep(1)


# ============================================================
# 看门狗入口
# ============================================================

def main():
    print()
    print(f"  XMU Tronclass 全自动签到 v1.1")
    print(f"  {'=' * 46}")
    print(f"  学号: {STUDENT_ID}")
    print(f"  登录: xmulogin 纯 API (无浏览器)")
    print(f"  {'=' * 46}")

    restart_count = 0
    restart_times = []

    while True:
        restart_count += 1
        # 重启时清空事件列表：否则界面会继续显示上一轮（可能是几小时前）的事件
        write_run_status(restart_count=restart_count, events=[])
        try:
            ok = main_loop()
            if ok is False:
                restart_times.append(time.time())
                restart_times = [t for t in restart_times if time.time() - t < 600]
                if len(restart_times) > 10:
                    print("[看门狗] 10 分钟内失败超过 10 次，放弃。")
                    break
                print(f"[看门狗] 60s 后重试 (第 {restart_count} 次)...")
                time.sleep(60)
                # 仅登录失败时清除 session（网络故障不删）
                if SESSION_FILE.exists():
                    SESSION_FILE.unlink()
                    print("[看门狗] 已清除过期 Session")
            else:
                # main_loop 正常永不返回；到这里说明异常退出
                break
        except KeyboardInterrupt:
            print("\n[看门狗] 用户中断，退出。")
            break
        except Exception as e:
            print(f"[看门狗] 崩溃: {e}")
            restart_times.append(time.time())
            restart_times = [t for t in restart_times if time.time() - t < 600]
            if len(restart_times) > 10:
                print("[看门狗] 10 分钟内崩溃超过 10 次，放弃。")
                break
            print(f"[看门狗] 5s 后重启 (第 {restart_count} 次)...")
            time.sleep(5)


if __name__ == "__main__":
    main()
