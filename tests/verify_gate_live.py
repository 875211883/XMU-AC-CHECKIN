# -*- coding: utf-8 -*-
"""门控有效性验证（只读，绝不提交签到）

做三件事：
1. 用真实会话拉取课程/点名，对每个"进行中且本人未签"的点名查一次名单，
   打印 已签/全班、比例、门控判定（等 / 开签）和判定理由；
2. 逐条复核门控边界（1/4、>20 人、等待超时、临近结束、拿不到名单）；
3. 汇总打印。

用法：python tests/verify_gate_live.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

import auto_checkin as ac  # noqa: E402

_TD30 = timedelta(minutes=30)


def _my_status(rid, session):
    """本人在某条点名名单里的 rollcall_status（读不到返回 None）"""
    try:
        r = session.get(f"{ac.BASE_URL}/api/rollcall/{rid}/student_rollcalls",
                        timeout=10)
        data = r.json()
        students = data.get("student_rollcalls") if isinstance(data, dict) else None
        me = ac._find_me(students or [])
        return (me or {}).get("rollcall_status")
    except Exception:
        return None


def main():
    print("=" * 72)
    print("门控参数")
    print(f"  开关 CHECKIN_GATE   = {ac.CHECKIN_GATE}")
    print(f"  比例阈值            = >{ac.CHECKIN_RATIO:.0%} 已签")
    print(f"  人数阈值            = >{ac.CHECKIN_MIN_SIGNED} 人已签")
    print(f"  最长等待            = {ac.CHECKIN_MAX_WAIT}s（超过直接签）")
    print(f"  临近结束            = 剩余 <{ac.CHECKIN_MIN_REMAINING}s 直接签")
    print("=" * 72)

    session, sid = ac.do_login()
    if not session or not sid:
        print("登录失败，无法做真实数据验证")
        return 1
    courses = ac.get_courses(session)
    print(f"课程 {len(courses)} 门（本脚本不做课程勾选过滤，全部只读检查）\n")

    rows = []
    for c in courses:
        cid = c.get("id")
        name = c.get("display_name") or c.get("name") or str(cid)
        try:
            rollcalls = ac.get_rollcalls(cid, session, sid)
        except Exception as e:
            print(f"  [!] {name}: 拉点名列表失败 {e}")
            continue
        for rc in rollcalls:
            rid = rc.get("id") or rc.get("rollcall_id")
            if not rid:
                continue
            is_num = bool(rc.get("is_number"))
            is_radar = bool(rc.get("is_radar"))
            if not (is_num or is_radar):
                continue
            try:
                _code, rc_status, end_time = ac.get_rollcall_detail(rid, session)
            except Exception:
                rc_status, end_time = None, None
            status = rc.get("status")
            if ac._is_terminal(status, end_time):
                continue
            if ac._rollcall_dead(rc, rc_status):
                continue
            signed, total, end_time2 = ac.get_signed_stats(rid, session)
            if signed is None:
                verdict = "开签（拿不到名单 → 保底直接签）"
                detail = "查不到人数"
            else:
                ratio = signed / total if total else 0
                need_wait, msg = ac.checkin_gate(rid, session, 0)
                verdict = ("等待中（未达标）" if need_wait else "开签")
                detail = (f"{signed}/{total} = {ratio:.1%}，"
                          f"{'未达' if need_wait else '达到'} "
                          f">25% 或 >20 人 → {msg}")
            rows.append((name, rid, ac.rollcall_label(rc), status, verdict, detail))

    print("真实数据检查结果（本人未签且点名未结束的点名）")
    print("-" * 72)
    if not rows:
        print("  当前没有「进行中且本人未签」的点名 → 门控此刻无事可做（正常）")
    for name, rid, label, status, verdict, detail in rows:
        print(f"  · {name} [{label}] rid={rid} 本人={status}")
        print(f"      判定: {verdict}")
        print(f"      依据: {detail}")
    print("-" * 72)

    # 用真实名单回验门控读数与阈值判定（取每门课最近一次有点名的记录）
    print("\n真实名单回验：门控读到的人数与判定口径")
    print("-" * 72)
    checked = 0
    for c in courses:
        cid = c.get("id")
        name = c.get("display_name") or c.get("name") or str(cid)
        try:
            rollcalls = ac.get_rollcalls(cid, session, sid)
        except Exception:
            continue
        for rc in rollcalls[:1]:                      # 列表按时间倒序，取最近一次
            rid = rc.get("id") or rc.get("rollcall_id")
            if not rid:
                continue
            signed, total, end = ac.get_signed_stats(rid, session)
            if signed is None:
                mine = _my_status(rid, session)
                if mine == ac.SIGNED_STUDENT_STATUS:
                    print(f"  · {name}: 本人已签（{mine}）→ 门控直接放行，不会重复提交")
                else:
                    print(f"  · {name}: 名单读不到 → 门控按「拿不到人数 → 直接签」保底")
                break
            ratio = signed / total if total else 0
            by_ratio = ratio > ac.CHECKIN_RATIO
            by_count = signed > ac.CHECKIN_MIN_SIGNED
            print(f"  · {name}: 已签 {signed}/{total} = {ratio:.1%} → "
                  f"门控口径 {'达标开签' if (by_ratio or by_count) else '未达标继续等'}"
                  f"（>{ac.CHECKIN_RATIO:.0%}? {by_ratio}，>{ac.CHECKIN_MIN_SIGNED}人? {by_count}）"
                  f" | 点名时间 {rc.get('rollcall_time')}")
            checked += 1
            break
    print(f"  共回验 {checked} 门课")
    print("-" * 72)

    print("\n门控边界复核（离线，用假名单）")
    print("-" * 72)
    cases = [
        (16, 64, 0, "恰好 1/4", True),
        (17, 64, 0, "刚过 1/4", False),
        (20, 200, 0, "恰好 20 人", True),
        (21, 200, 0, "刚过 20 人", False),
        (0, 70, 0, "无人已签", True),
        (1, 70, 121, "等待超 120s 保底", False),
        (1, 70, 119, "等待未超时仍等", True),
    ]
    ok_all = True
    for signed, total, waited, label, expect_wait in cases:
        class FakeResp:
            def __init__(self, payload):
                self._p, self.status_code = payload, 200

            def json(self):
                return self._p

        class FakeSess:
            def __init__(self, payload):
                self._p = payload

            def get(self, url, timeout=10):
                return FakeResp(self._p)

        body = {"status": "on_going",
                # 必须给未来的 end_time，否则会命中"临近结束直接签"保底，
                # 把 1/4、20 人这些边界用例全部放行（假数据的坑）
                "end_time": (datetime.now(timezone.utc)
                             .replace(microsecond=0) + _TD30).isoformat()
                             .replace("+00:00", "Z"),
                "student_rollcalls": [
                    {"user_no": f"s{i}", "rollcall_status":
                     ("on_call_fine" if i < signed else "absent")}
                    for i in range(total)]}
        need_wait, msg = ac.checkin_gate(1, FakeSess(body), waited)
        hit = (need_wait == expect_wait)
        ok_all &= hit
        print(f"  [{'ok ' if hit else 'FAIL'}] {label}: "
              f"{'等待' if need_wait else '开签'} ← {msg}")
    print("-" * 72)
    print(f"\n结论: 边界复核 {'全部通过' if ok_all else '有不符项'}"
          f"；真实点名检查 {len(rows)} 条")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
