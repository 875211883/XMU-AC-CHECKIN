# -*- coding: utf-8 -*-
"""生成文档用界面截图：全部使用假数据 —— 不联网、不读也不写任何个人配置。

用法：
    python tools/demo_ui.py
窗口会保持打开（标题就是正常标题），外部截图后关掉即可。

实现要点：把 control_panel 的所有数据文件路径重定向到临时目录，
并把 App._load 换成假数据版本，因此不会碰 creds.env / schedule.json / checkin_record.json。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import control_panel as cp  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="xmu_demo_"))
cp.BASE = TMP
cp.COURSE_FILE = TMP / "course_filter.json"
cp.SCHEDULE_FILE = TMP / "schedule.json"
cp.RECORD_FILE = TMP / "checkin_record.json"
cp.LOG_FILE = TMP / "checkin.log"
cp.LAUNCH_FILE = TMP / "launch_count.json"
cp.RUN_STATUS_FILE = TMP / "auto_checkin_status.json"
cp.PID_FILE = TMP / "auto_checkin.pid"
cp.START_FILE = TMP / "start_time.txt"

# 假课程（通用课名，不是任何人的真实课表）
COURSES = [{"id": 900 + i, "name": n} for i, n in enumerate([
    "高等数学 A", "大学物理 B", "程序设计基础", "线性代数",
    "英语听说", "体育（篮球）", "思想道德与法治", "概率论与数理统计",
])]
# 演示"部分勾选"：后两门不监控
cp.COURSE_FILE.write_text(json.dumps({"disabled": ["906", "907"]},
                                     ensure_ascii=False, indent=2),
                          encoding="utf-8")
cp.SCHEDULE_FILE.write_text(json.dumps({
    "enabled": True,
    "slots": [{"start": "07:30", "stop": "12:00", "days": "weekday"},
              {"start": "14:00", "stop": "21:30", "days": "weekend"}],
}, ensure_ascii=False, indent=2), encoding="utf-8")
_today = time.strftime("%Y-%m-%d")
cp.RECORD_FILE.write_text(json.dumps([
    {"time": f"{_today} 08:12:03", "type": "数字", "course": "高等数学 A",
     "detail": "码=0839"},
    {"time": f"{_today} 10:05:41", "type": "GPS", "course": "大学物理 B",
     "detail": "(24.4392, 118.0977)"},
], ensure_ascii=False), encoding="utf-8")
cp.LAUNCH_FILE.write_text('{"count": 12}', encoding="utf-8")
cp.START_FILE.write_text(str(time.time() - 4830), encoding="utf-8")
cp.RUN_STATUS_FILE.write_text(json.dumps({
    "restart_count": 1,
    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "query_total": 168,
    "course_queries": {c["name"]: 24 for c in COURSES},
    "events": [
        "[NEW] 高等数学 A (数字)",
        "[GO] 高等数学 A 已签 41/96，达标开签",
        "[OK] 数字 高等数学 A 码=0839",
        "[NEW] 体育（篮球） (GPS)",
        "[WAIT] 体育（篮球） 已签 12/60，未达标继续等",
        "[GO] 体育（篮球） 已签 22/60，达标开签",
        "[OK] GPS 体育（篮球） (24.4392, 118.0977)",
    ],
}, ensure_ascii=False), encoding="utf-8")

import app  # noqa: E402


def fake_load(self):
    """替代真实 _load：不联网、不读真实文件"""
    try:
        data = {
            "running": 12345,
            "today": cp.today_done(),
            "events": [],
            "sched": cp.schedule_load(),
            "course_count": len(COURSES) - len(cp.course_filter_load()["disabled"]),
            "all_total": len(cp.load_json_file(cp.RECORD_FILE, [])),
            "launches": cp.launch_count(),
            "run_status": cp.run_status(),
        }
        self._courses = COURSES
        self._course_count = len(COURSES)
        self.after(0, self._render, data)
    finally:
        self._busy = False


app.App._load = fake_load
app.App._ensure_alive = lambda self, running: None   # 演示不碰真实进程

a = app.App()
a._armed = True
a.geometry("1020x830+60+60")
print("演示窗口已打开（假数据）。截图后关闭窗口即可。")
a.mainloop()
