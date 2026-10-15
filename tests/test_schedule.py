# 离线单测：课程勾选 + 定时（时段判定 / 下次切换 / 任务 XML）
# 不联网、不注册真实计划任务（_delete_all_tasks/_create_task 被替换成假实现）
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import control_panel as cp  # noqa: E402

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


# 配置路径指向临时目录，避免污染项目里的真实配置
_tmp = tempfile.TemporaryDirectory()
cp.COURSE_FILE = Path(_tmp.name) / "course_filter.json"
cp.SCHEDULE_FILE = Path(_tmp.name) / "schedule.json"

print("== 课程勾选 ==")
check("默认全选（无例外文件）", cp.course_filter_load()["disabled"] == [])
n = cp.course_filter_save([120753, "119921", 120753, "  ", ""])
check("保存去重且转字符串",
      n == 2 and cp.course_filter_load()["disabled"] == ["120753", "119921"],
      str(cp.course_filter_load()))
courses = [{"id": 120753, "name": "新生教务学务一点通"},
           {"id": 106877, "name": "气排球（基础班1）"},
           {"id": "119921", "name": "军事技能"}]
kept = cp.filter_courses(courses)
check("未勾选的课被过滤", [c["id"] for c in kept] == [106877], str(kept))
check("监控门数/总门数", cp.course_monitor_count(courses) == (1, 3),
      str(cp.course_monitor_count(courses)))
cp.course_filter_save([])
check("清空例外 → 全部监控", len(cp.filter_courses(courses)) == 3)
cp.COURSE_FILE.write_text("{坏文件", encoding="utf-8")
check("文件损坏不炸且按全选", len(cp.filter_courses(courses)) == 3)
cp.COURSE_FILE.unlink()

print("== 时间解析 / 重复方式 ==")
check("7:30 → 07:30", cp._norm_hhmm("7:30") == "07:30")
check("25:00 → None", cp._norm_hhmm("25:00") is None)
check("空 → None", cp._norm_hhmm("") is None)
check("01:30 → 90 分", cp._minutes("01:30") == 90)
check("每天=7天", cp._slot_days({"days": "daily"}) == [0, 1, 2, 3, 4, 5, 6])
check("工作日=周一~五", cp._slot_days({"days": "weekday"}) == [0, 1, 2, 3, 4])
check("周末=周六日", cp._slot_days({"days": "weekend"}) == [5, 6])
check("未知值回落每天", cp._slot_days({"days": "??"}) == [0, 1, 2, 3, 4, 5, 6])

print("== 时段窗口判定 ==")
MON9 = datetime(2026, 10, 19, 9, 0)      # 周一
MON8 = datetime(2026, 10, 19, 8, 0)
MON18 = datetime(2026, 10, 19, 18, 0)
SAT9 = datetime(2026, 10, 24, 9, 0)      # 周六
wd = {"start": "08:00", "stop": "18:00", "days": "weekday"}
we = {"start": "08:00", "stop": "18:00", "days": "weekend"}
check("工作日周一命中", cp.schedule_window_active(wd, MON9))
check("工作日周六不命中", not cp.schedule_window_active(wd, SAT9))
check("周末周六命中", cp.schedule_window_active(we, SAT9))
check("周末周一不命中", not cp.schedule_window_active(we, MON9))
check("起始边界含（08:00）", cp.schedule_window_active(wd, MON8))
check("结束边界不含（18:00）", not cp.schedule_window_active(wd, MON18))
check("只有 start → 之后一直有效",
      cp.schedule_window_active({"start": "08:00"}, datetime(2026, 10, 19, 23, 0)))
check("只有 stop → 之前一直有效",
      cp.schedule_window_active({"stop": "08:00"}, datetime(2026, 10, 19, 7, 0)))
cross = {"start": "22:00", "stop": "02:00", "days": "daily"}
check("跨天 前半夜命中", cp.schedule_window_active(cross, datetime(2026, 10, 19, 23, 0)))
check("跨天 后半夜命中", cp.schedule_window_active(cross, datetime(2026, 10, 20, 1, 0)))
check("跨天 白天不命中", not cp.schedule_window_active(cross, datetime(2026, 10, 20, 12, 0)))
check("start==stop 视为无效",
      not cp.schedule_window_active({"start": "08:00", "stop": "08:00"}, MON9))
check("两者都空 → 不命中", not cp.schedule_window_active({}, MON9))

print("== 定时是否生效 / 下次切换 / 文案 ==")
cp.SCHEDULE_FILE.write_text(json.dumps({"enabled": False, "slots": []}),
                            encoding="utf-8")
check("未启用定时 → 不受时段限制", cp.schedule_active_now() is True)
check("未启用 → 无下次切换", cp.schedule_next_change() == (None, None))
check("文案提示 7×24", "7×24" in cp.schedule_status_text())

cp.SCHEDULE_FILE.write_text(json.dumps({"enabled": True, "slots": [
    {"start": "08:00", "stop": "12:00", "days": "weekday"},
    {"start": "14:00", "stop": "18:00", "days": "weekday"}]}, ensure_ascii=False),
    encoding="utf-8")
check("周一10点 在时段内", cp.schedule_active_now(datetime(2026, 10, 19, 10, 0)))
check("周一13点 不在时段内", not cp.schedule_active_now(datetime(2026, 10, 19, 13, 0)))
check("周六10点 不在时段内", not cp.schedule_active_now(datetime(2026, 10, 24, 10, 0)))
nxt, kind = cp.schedule_next_change(datetime(2026, 10, 19, 13, 0))
check("13点→下次 14:00 启动",
      nxt == datetime(2026, 10, 19, 14, 0) and kind == "start", f"{nxt} {kind}")
nxt, kind = cp.schedule_next_change(datetime(2026, 10, 19, 20, 0))
check("周一20点→下次周二 08:00 启动（跳过周末）",
      nxt == datetime(2026, 10, 20, 8, 0) and kind == "start", f"{nxt} {kind}")
check("状态文案含重复方式", "工作日" in cp.schedule_status_text(datetime(2026, 10, 19, 10, 0)))
check("旧格式 {start,stop} 能读", (cp.SCHEDULE_FILE.write_text(
    json.dumps({"enabled": True, "start": "06:00", "stop": "23:30"}),
    encoding="utf-8") or cp.schedule_load()["slots"][0]["start"] == "06:00"))

print("== 任务 XML（不注册到系统）==")
xml_wd = cp._task_xml("start", "07:30", "weekday", "单测")
check("工作日用 ScheduleByWeek", "<ScheduleByWeek>" in xml_wd)
check("含周一~周五", all(f"<{d}/>" in xml_wd for d in
                         ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")))
check("不含周六日", "<Saturday/>" not in xml_wd and "<Sunday/>" not in xml_wd)
check("每天用 ScheduleByDay", "<ScheduleByDay>" in cp._task_xml("stop", "23:30", "daily", "单测"))
check("允许电池启动", "<DisallowStartIfOnBatteries>false" in xml_wd)
check("电池时不停止", "<StopIfGoingOnBatteries>false" in xml_wd)
check("错过时间补跑", "<StartWhenAvailable>true" in xml_wd)
check("忽略重复实例", "<MultipleInstancesPolicy>IgnoreNew" in xml_wd)
check("触发器时间正确", "<StartBoundary>" in xml_wd and "T07:30:00" in xml_wd)
check("含动作 start", "start" in xml_wd.split("<Arguments>")[1].split("</Arguments>")[0])

print("== schedule_apply 校验与落盘（假任务实现）==")
_real_del, _real_create = cp._delete_all_tasks, cp._create_task
cp._delete_all_tasks = lambda: None
created = []
cp._create_task = lambda name, action, hhmm, days="daily": (
    created.append((name, action, hhmm, days)) or (True, ""))
check("非法时间被拒且不改动",
      "未做任何更改" in cp.schedule_apply([{"start": "8点", "stop": "18:00"}]))
check("全空被拒", "没有有效时间段" in cp.schedule_apply([{"start": "", "stop": ""}]))
msg = cp.schedule_apply([{"start": "7:30", "stop": "22:00", "days": "weekday"},
                         {"start": "09:00", "stop": "21:00", "days": "weekend"}])
check("两个时段共注册 4 个任务", len(created) == 4, str(created))
check("任务名带编号", created[0][0] == "XMU-AC-AutoStart-1" and
      created[3][0] == "XMU-AC-AutoStop-2", str(created))
check("时间被规范化 07:30", created[0][2] == "07:30")
check("days 透传到任务", created[0][3] == "weekday" and created[2][3] == "weekend")
check("消息含已设置", "已设置" in msg and "时段1（工作日）启动 07:30" in msg, msg)
saved = json.loads(cp.SCHEDULE_FILE.read_text(encoding="utf-8"))
check("落盘 enabled=True", saved["enabled"] is True)
check("落盘 slots 含 days", saved["slots"][0]["days"] == "weekday" and
      saved["slots"][1]["start"] == "09:00", str(saved))
cp._delete_all_tasks, cp._create_task = _real_del, _real_create
_tmp.cleanup()

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
