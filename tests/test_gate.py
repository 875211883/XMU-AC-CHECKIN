# 离线单测：签到门控逻辑（不联网，Fake session）
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("STUDENT_ID", "test")
os.environ.setdefault("PASSWORD", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auto_checkin as ac  # noqa: E402

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


class FakeResp:
    def __init__(self, payload, code=200):
        self._p, self.status_code = payload, code

    def json(self):
        return self._p


class FakeSess:
    def __init__(self, payload, code=200):
        self._p, self._c = payload, code

    def get(self, url, timeout=10):
        return FakeResp(self._p, self._c)


DEFAULT_END = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def roster(statuses, end_time=DEFAULT_END):
    # 每条都带 updated_at（与本校实测一致：缺勤学生也有 updated_at）
    return {
        "status": "on_going",
        "end_time": end_time,
        "student_rollcalls": [
            {"user_no": f"s{i}", "rollcall_status": st, "status": "on_call",
             "updated_at": "2026-09-20T08:00:00Z"}
            for i, st in enumerate(statuses)
        ],
    }


FINE, ABSENT, LEAVE = "on_call_fine", "absent", "on_personal_leave"

print("== get_signed_stats 计数 ==")
s = FakeSess(roster([FINE, FINE, ABSENT, LEAVE, FINE]))
signed, total, et = ac.get_signed_stats(1, s)
check("只认 on_call_fine", (signed, total) == (3, 5), f"got {signed}/{total}")
check("end_time 透传", et == DEFAULT_END)
s = FakeSess(roster([ABSENT] * 4))  # 全员未签但都有 updated_at（上游 e8b05cc2 的坑）
signed, total, _ = ac.get_signed_stats(1, s)
check("updated_at 不计入（防上游坑）", (signed, total) == (0, 4), f"got {signed}/{total}")
s = FakeSess(roster([]), code=200)
check("空名单 → None", ac.get_signed_stats(1, s) == (None, None, None))
s = FakeSess(roster([FINE]), code=500)
check("非200 → None", ac.get_signed_stats(1, s) == (None, None, None))

print("== checkin_gate 边界 ==")


def gate(signed_n, total_n, waited=0, end_time=DEFAULT_END, code=200):
    body = roster([FINE] * signed_n + [ABSENT] * (total_n - signed_n), end_time)
    return ac.checkin_gate(1, FakeSess(body, code), waited)


w, m = gate(16, 64)   # 恰好 1/4
check("恰好1/4 不放行", w is True, m)
w, m = gate(17, 64)   # 17/64 > 1/4
check("超过1/4 放行", w is False, m)
w, m = gate(17, 70)
check("70人 17人等", w is True, m)
w, m = gate(18, 70)
check("70人 18人签", w is False, m)
w, m = gate(20, 200)  # 恰好 20
check("恰好20 不放行", w is True, m)
w, m = gate(21, 200)
check("21人 放行(>20)", w is False, m)
w, m = gate(0, 70)
check("0人 等待", w is True, m)

print("== 保底逻辑 ==")
w, m = gate(1, 70, waited=121)
check("超时放行", w is False and "120" in m, m)
w, m = gate(1, 70, waited=119)
check("未超时仍等", w is True, m)
soon = (datetime.now(timezone.utc) + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
w, m = gate(1, 70, end_time=soon)
check("快截止放行", w is False and "剩余" in m, m)
w, m = gate(1, 70, code=500)
check("查不到人数放行", w is False and "查不到" in m, m)

ac.CHECKIN_GATE = False
w, m = gate(0, 70)
check("总开关关闭=发现即签", w is False and m == "", m)
ac.CHECKIN_GATE = True

print("== _time_remaining / _end_time_passed ==")
check("过去时间→负", ac._time_remaining("2020-01-01T00:00:00Z") < 0)
check("无时间→None", ac._time_remaining(None) is None)
check("垃圾→None", ac._time_remaining("garbage") is None)
fut = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
check("未来时间→正", ac._time_remaining(fut) > 0)
check("_end_time_passed 未来=False", ac._end_time_passed(fut) is False)
check("_end_time_passed 过去=True", ac._end_time_passed("2020-01-01T00:00:00Z") is True)
check("_end_time_passed 无=False", ac._end_time_passed(None) is False)

print("== v1.2 新增：_find_me / 自校 / absent 口径 ==")
# _find_me：按 user_no 找本人（STUDENT_ID 环境变量是 'test'）
roster_mixed = [
    {"user_no": "s1", "rollcall_status": "on_call_fine"},
    {"user_no": "test", "rollcall_status": "absent"},
    {"user_no": "s3", "rollcall_status": None},
]
check("_find_me 命中本人", ac._find_me(roster_mixed) is roster_mixed[1])
check("_find_me 缺失→None", ac._find_me(roster_mixed[:1] + roster_mixed[2:]) is None)

# 自校：本人已在名单里签上（on_call_fine）→ get_signed_stats 返回 None（放行信号）
body_self = {
    "status": "on_going", "end_time": DEFAULT_END,
    "student_rollcalls": [
        {"user_no": "s1", "rollcall_status": "on_call_fine", "updated_at": "x"},
        {"user_no": "test", "rollcall_status": "on_call_fine", "updated_at": "x"},
        {"user_no": "s2", "rollcall_status": "absent", "updated_at": "x"},
    ],
}
check("自校:本人已签→None放行", ac.get_signed_stats(1, FakeSess(body_self)) == (None, None, None))

# absent 状态应归入活跃集（雷达等待期不再刷 [?] 未知状态）
check("absent 属活跃状态", "absent" in ac.ACTIVE_STATUSES)
check("absent 非终端", not ac._is_terminal("absent", None))
check("on_call_fine 仍终端", ac._is_terminal("on_call_fine", None))

# 空/非列表名单防护（除零路径）
for name, body in [
    ("非dict响应→None", ["x", "y"]),
    ("名单全非dict→None", {"student_rollcalls": ["a", 1, None]}),
]:
    st_, code_ = body if isinstance(body, list) else (body, 200)
    r = ac.get_signed_stats(1, FakeSess(body, 200))
    check(name, r == (None, None, None), str(r))

print("== v1.3 新增：已结束点名判定（界面被单一课程刷屏的根因）==")
# 实测样本：昨天的点名，列表接口仍返回 is_expired=false、本人 status=absent，
# 但 rollcall_status 已是 finished、详情 end_time 为 null
yesterday_rc = {
    "id": 454185, "is_number": True, "is_radar": False, "is_expired": False,
    "status": "absent", "rollcall_status": "finished", "source": "number",
    "rollcall_time": "2026-10-14T08:24:00Z", "student_status": "absent",
    "title": "2026.10.14 16:24",
}
check("列表 rollcall_status=finished → 已结束", ac._rollcall_dead(yesterday_rc))
check("详情 status=finished → 已结束",
      ac._rollcall_dead({"status": "absent"}, "finished"))
check("is_expired=True → 已结束", ac._rollcall_dead({"is_expired": True}))
check("进行中的点名不误判",
      not ac._rollcall_dead({"status": "absent", "rollcall_status": "in_progress",
                             "is_expired": False}, "in_progress"))
check("字段缺失不误判", not ac._rollcall_dead({}))
check("已结束但本人已签 → 仍是终端",
      ac._is_terminal("on_call_fine", None))

old = (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
check("_rollcall_age 8小时前≈28800s", 28000 < ac._rollcall_age(old) < 30000)
check("_rollcall_age 5分钟前<600s", ac._rollcall_age(fresh) < 600)
check("_rollcall_age 垃圾→None", ac._rollcall_age("garbage") is None)
check("_rollcall_age 空→None", ac._rollcall_age(None) is None)
check("号码放弃阈值=6小时", ac.CODE_GIVEUP_AGE == 21600)

print("== v1.3 新增：诊断文件去重 / 封顶 ==")
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "code_missing_diag.json"
    check("不存在→False", ac._diag_has_rid(_p, 1) is False)
    _p.write_text('{"rid": 454185, "course": "x"}\n', encoding="utf-8")
    check("已记录→True", ac._diag_has_rid(_p, 454185) is True)
    check("未记录→False", ac._diag_has_rid(_p, 999) is False)
    _p.write_text("\n".join('{"rid": %d}' % i for i in range(5000)) + "\n",
                  encoding="utf-8")
    ac._cap_diag_file(_p, max_bytes=1024, keep_lines=10)
    check("超限后只留最后 10 行",
          len(_p.read_text(encoding="utf-8").splitlines()) == 10)

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
