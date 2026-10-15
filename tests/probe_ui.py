"""离线自检：把新界面（课程勾选 + 定时卡片）真的建起来并跑一遍渲染路径。
不联网、不启动监控、不写项目配置（配置路径指向临时目录）。"""
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, r"D:\签到\XMU-AC-master")
import control_panel as cp  # noqa: E402

tmp = tempfile.TemporaryDirectory()
cp.COURSE_FILE = Path(tmp.name) / "course_filter.json"
cp.SCHEDULE_FILE = Path(tmp.name) / "schedule.json"

import app  # noqa: E402

app.App._ensure_alive_real = app.App._ensure_alive
app.App._ensure_alive = lambda self, running: None      # 构造阶段别真去拉起监控
app.App._toast = lambda self, msg: print("   [toast]", (msg or "").replace("\n", " | ")[:120])
# 绝不碰真实计划任务
cp._delete_all_tasks = lambda: None
cp._create_task = lambda *a, **k: (True, "")

ok = True
try:
    a = app.App()
    a.update_idletasks()

    # 1) 课程勾选：注入 12 门课，其中 5 门预先取消勾选
    a._courses = [{"id": 120753, "name": "新生教务学务一点通"},
                  {"id": 106877, "name": "气排球（基础班1）"},
                  {"id": 106982, "name": "微积分I-1"},
                  {"id": 119921, "name": "军事技能"},
                  {"id": 108923, "name": "大学英语A1"},
                  {"id": 115127, "name": "国家安全教育"},
                  {"id": 113807, "name": "习近平新时代…概论"},
                  {"id": 111848, "name": "线性代数(A)"},
                  {"id": 111905, "name": "新生研讨课"},
                  {"id": 111920, "name": "Python程序设计"},
                  {"id": 114530, "name": "计算思维导论 I"},
                  {"id": 118998, "name": "26级大学英语分类考试4"}]
    cp.course_filter_save([120753, 119921, 115127, 111920, 118998])
    a._courses_drawn = False
    a._fill_courses()
    a.update_idletasks()
    vars_ = a.course_vars
    print("1) 复选框数量 =", len(vars_))
    checked = [cid for cid, v in vars_.items() if v.get()]
    print("   勾选 =", len(checked), checked)
    print("   摘要 =", a.course_summary.cget("text"))
    ok &= len(vars_) == 12 and len(checked) == 7

    # 2) 保存勾选（写临时配置）
    a.save_courses()
    saved = cp.course_filter_load()["disabled"]
    print("2) 保存后 disabled =", saved)
    ok &= sorted(saved) == sorted(["120753", "119921", "115127", "111920", "118998"])

    # 3) 定时行：注入两个时段（工作日/周末）并读回
    for (row, *_rest) in list(a.slot_rows):
        row.destroy()
    a.slot_rows = []
    a.add_slot_row("07:30", "12:00", "weekday")
    a.add_slot_row("14:00", "21:30", "weekend")
    a.update_idletasks()
    slots = a._collect_slots()
    print("3) _collect_slots =", slots)
    ok &= slots == [{"start": "07:30", "stop": "12:00", "days": "weekday"},
                    {"start": "14:00", "stop": "21:30", "days": "weekend"}]

    # 4) 渲染路径（含定时状态文案、课程卡片、hero）
    cp.SCHEDULE_FILE.write_text(
        '{"enabled": true, "slots": [{"start": "00:00", "stop": "23:59", '
        '"days": "daily"}]}', encoding="utf-8")
    data = {"running": 4242, "today": {}, "events": [], "sched": cp.schedule_load(),
            "course_count": 7, "all_total": 2, "launches": 36,
            "run_status": {"restart_count": 1, "query_total": 24,
                           "course_queries": {"气排球（基础班1）": 4}, "events": []}}
    a._render(data)
    a.update_idletasks()
    print("4) 定时文案 =", a.sched.cget("text"))
    print("   hero =", a._hero_title, "/", a._hero_sub)
    ok &= "在时段内" in a.sched.cget("text") or "不在时段内" in a.sched.cget("text")

    # 5) 未运行时文案：手动停止 / 未点启动（软件不会自动开始）
    a._user_stopped = True
    data["running"] = None
    a._render(data)
    a.update_idletasks()
    print("5a) 手动停止 hero =", a._hero_title, "/", a._hero_sub)
    ok &= a._hero_title == "已手动停止"

    a._user_stopped = False
    a._armed = False
    a._render(data)
    a.update_idletasks()
    print("5b) 未启动 hero =", a._hero_title, "/", a._hero_sub)
    ok &= "不会自动开始" in a._hero_sub

    # 6) 自动拉起策略：没点启动不拉起 / 时段外不拉起 / 手动停止不拉起 /
    #    点过启动 + 时段内才拉起（并有 30 秒节流）
    calls = []
    app.App.ensure_running = lambda self: calls.append(1)
    real = app.App._ensure_alive_real
    inside = ('{"enabled": true, "slots": [{"start": "00:00", "stop": "23:59", '
              '"days": "daily"}]}')
    outside = ('{"enabled": true, "slots": [{"start": "00:00", "stop": "00:01", '
               '"days": "daily"}]}')

    cp.SCHEDULE_FILE.write_text(inside, encoding="utf-8")
    a._armed = False
    a._user_stopped = False
    a._last_ensure = 0.0
    real(a, False)
    unarmed = len(calls)

    a._armed = True
    cp.SCHEDULE_FILE.write_text(outside, encoding="utf-8")
    a._last_ensure = 0.0
    real(a, False)
    outside_n = len(calls)

    cp.SCHEDULE_FILE.write_text(inside, encoding="utf-8")
    a._user_stopped = True
    a._last_ensure = 0.0
    real(a, False)
    manual = len(calls)

    a._user_stopped = False
    a._last_ensure = 0.0
    real(a, False)
    startable = len(calls)
    real(a, False)
    throttled = len(calls)
    print(f"6) 未启动={unarmed} 时段外={outside_n} 手动停止={manual} "
          f"时段内={startable} 节流后={throttled}")
    ok &= (unarmed, outside_n, manual, startable, throttled) == (0, 0, 0, 1, 1)

    a.destroy()
except Exception:
    traceback.print_exc()
    ok = False

print("PROBE", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
