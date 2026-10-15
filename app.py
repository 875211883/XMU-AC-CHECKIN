# -*- coding: utf-8 -*-
"""
XMU 畅课自动签到 —— 图形主界面（单页 / App 风格）
无课程表；显示运行指标：外部启动次数、看门狗重启次数、查询次数（含每门课）。
"""
import os
import sys
import time
import ctypes
import threading
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

import control_panel as cp

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

REFRESH_MS = 3000


class App(tk.Tk):
    THEMES = {
        "light": {
            "bg": "#f3f5fb", "card": "#ffffff", "primary": "#2563eb",
            "primary_soft": "#eaf1ff", "text": "#1c2333", "muted": "#6b7488",
            "border": "#e6e9f2", "ok": "#16a34a", "danger": "#dc2626",
        },
        "dark": {
            "bg": "#0e1320", "card": "#181f30", "primary": "#3b82f6",
            "primary_soft": "#1c2740", "text": "#e8ecf5", "muted": "#929db3",
            "border": "#26304a", "ok": "#34d399", "danger": "#f87171",
        },
    }

    def __init__(self):
        super().__init__()
        self.title("XMU 畅课自动签到")
        self.geometry("1020x760")
        self.minsize(940, 660)
        self._dark = True
        self.c = self.THEMES["dark"]
        # 状态字段（_build_ui 里就会用到，必须先于建界面初始化）
        self._busy = False
        self._hero_running = False
        self._last_pid = None
        self._course_count = 0
        self._courses = []          # [{'id','name'}] 登录后拉取
        self._courses_drawn = False
        self._user_stopped = False  # 手动停止后不再自动拉起
        self._armed = False         # 只有用户自己点过「启动」才允许自动拉起
        self._qr_notified = set()   # 已响铃提醒过的二维码点名 rid
        self._qr_title = False      # 窗口标题是否处于二维码告警态
        self._qr_session = None     # 复用登录会话（粘贴提交用）
        self._qr_sid = None
        self._breath = 0
        self._configure_styles()
        self._build_ui()
        self.auto_refresh()
        # 打开界面不再自动开始签到（等用户自己点「启动 / 重启」）。
        # 定时计划任务是独立通道：到点仍会按 schedule.json 启动/停止。

    def ensure_running(self):
        def _run():
            try:
                if not cp.find_running():
                    cp.start()
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    # ---------------- 样式 ----------------
    def _px(self, v):
        # 1 point 的像素数（高 DPI 下 >1），据此换算
        try:
            return int(v * self.winfo_fpixels("1p"))
        except Exception:
            return v

    def _configure_styles(self):
        c = self.c
        self.configure(bg=c["bg"])
        f = ("Microsoft YaHei UI", 10)
        self.font = f
        self.font_head = ("Microsoft YaHei UI", 13, "bold")
        self.font_title = ("Microsoft YaHei UI", 15, "bold")
        self.font_num = ("Microsoft YaHei UI", 18, "bold")
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=c["bg"], foreground=c["text"], font=f)
        st.configure("TFrame", background=c["bg"])
        st.configure("Card.TFrame", background=c["card"])
        st.configure("TLabel", background=c["bg"], foreground=c["text"])
        st.configure("Card.TLabel", background=c["card"], foreground=c["text"])
        st.configure("Head.TLabel", background=c["card"], foreground=c["text"],
                     font=self.font_head)
        st.configure("Muted.TLabel", background=c["card"], foreground=c["muted"])
        st.configure("MutedBg.TLabel", background=c["bg"], foreground=c["muted"])
        st.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                     padding=(14, 8))
        st.configure("Ghost.TButton", font=("Microsoft YaHei UI", 10),
                     padding=(10, 7))
        st.configure("Danger.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                     padding=(14, 8))
        st.configure("TEntry", fieldbackground=c["card"], foreground=c["text"],
                     insertcolor=c["text"])
        # 时间/重复选择框（深色下也要看得清）
        st.configure("TCombobox", fieldbackground=c["card"], background=c["bg"],
                     foreground=c["text"], arrowcolor=c["text"],
                     selectbackground=c["primary_soft"],
                     selectforeground=c["text"], padding=2)
        try:
            self.option_add("*TCombobox*Listbox.background", c["card"])
            self.option_add("*TCombobox*Listbox.foreground", c["text"])
            self.option_add("*TCombobox*Listbox.selectBackground", c["primary"])
            self.option_add("*TCombobox*Listbox.selectForeground", "white")
        except Exception:
            pass

    def _build_ui(self):
        c = self.c
        # 顶部标题栏
        topbar = tk.Frame(self, bg=c["card"], height=64)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        tk.Label(topbar, text="🎓  XMU 畅课自动签到", bg=c["card"], fg=c["text"],
                 font=self.font_title).pack(side="left", padx=22)
        self.theme_btn = tk.Button(
            topbar, text="☀ 浅色", bg=c["card"], fg=c["muted"], bd=0,
            activebackground=c["card"], activeforeground=c["text"],
            font=("Microsoft YaHei UI", 10), command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=18)

        # 滚动内容
        outer = tk.Frame(self, bg=c["bg"])
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=c["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(canvas, bg=c["bg"])
        win = canvas.create_window((0, 0), window=self.content, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        self.content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._wrap_mousewheel(canvas)

        body = tk.Frame(self.content, bg=c["bg"])
        body.pack(fill="x", padx=22, pady=20)

        self._build_hero(body)
        self._build_stats(body)
        self._build_actions(body)
        self._build_middle(body)
        self._build_courses(body)
        self._build_qr(body)
        self._build_account(body)
        self._build_log(body)

    def _wrap_mousewheel(self, canvas):
        def _on(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on)

    # ---------------- 各区域 ----------------
    def _build_hero(self, parent):
        c = self.c
        self.hero = tk.Canvas(parent, height=self._px(130), bg=c["card"],
                              highlightthickness=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda e: self._paint_hero())

    def _build_stats(self, parent):
        c = self.c
        stats = tk.Frame(parent, bg=c["bg"])
        stats.pack(fill="x", pady=(14, 0))
        for i in range(6):
            stats.columnconfigure(i, weight=1, uniform="s")
        self.stat_num = []
        titles = ["今日签到", "累计签到", "监控课程",
                  "外部启动", "看门狗重启", "实时尝试"]
        for i, t in enumerate(titles):
            card = tk.Frame(stats, bg=c["card"], highlightbackground=c["border"],
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i < 5 else 0)
            num = tk.Label(card, text="0", bg=c["card"], fg=c["text"],
                           font=self.font_num)
            num.pack(anchor="w", padx=12, pady=(10, 0))
            tk.Label(card, text=t, bg=c["card"], fg=c["muted"],
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12,
                                                          pady=(0, 10))
            self.stat_num.append(num)

    def _build_actions(self, parent):
        c = self.c
        bar = tk.Frame(parent, bg=c["bg"])
        bar.pack(fill="x", pady=16)
        tk.Button(bar, text="启动 / 重启", bg=c["primary"], fg="white",
                  font=("Microsoft YaHei UI", 11, "bold"), bd=0,
                  activebackground=c["primary"], activeforeground="white",
                  padx=22, pady=8, command=self.do_start).pack(side="left")
        tk.Button(bar, text="停  止", bg=c["danger"], fg="white",
                  font=("Microsoft YaHei UI", 11, "bold"), bd=0,
                  activebackground=c["danger"], activeforeground="white",
                  padx=22, pady=8, command=self.do_stop).pack(side="left", padx=12)

    def _build_middle(self, parent):
        c = self.c
        mid = tk.Frame(parent, bg=c["bg"])
        mid.pack(fill="x")
        mid.columnconfigure(0, weight=1, uniform="m")
        mid.columnconfigure(1, weight=1, uniform="m")

        # 定时卡片
        scard = tk.Frame(mid, bg=c["card"], highlightbackground=c["border"],
                         highlightthickness=1)
        scard.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        pad = tk.Frame(scard, bg=c["card"])
        pad.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(pad, text="⏰  定时开关（可多时段）", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(anchor="w")
        hdr = tk.Frame(pad, bg=c["card"])
        hdr.pack(fill="x", pady=(10, 0))
        for txt, padx in (("启动", (2, 26)), ("关闭", (0, 22)), ("重复", (0, 0))):
            tk.Label(hdr, text=txt, bg=c["card"], fg=c["muted"],
                     font=("Microsoft YaHei UI", 9)).pack(side="left",
                                                          padx=padx)
        self.slot_container = tk.Frame(pad, bg=c["card"])
        self.slot_container.pack(fill="x")
        self.slot_rows = []
        self.sched = tk.Label(pad, text="", bg=c["card"], fg=c["muted"],
                              justify="left", wraplength=430)
        self.sched.pack(anchor="w", pady=(8, 0))
        brow = tk.Frame(pad, bg=c["card"])
        brow.pack(fill="x", pady=(10, 0))
        tk.Button(brow, text="＋ 添加时段", bg=c["bg"], fg=c["text"], bd=0,
                  font=("Microsoft YaHei UI", 9), padx=10, pady=5,
                  command=lambda: self.add_slot_row()).pack(side="left")
        tk.Button(brow, text="保存并注册", bg=c["primary"], fg="white", bd=0,
                  font=("Microsoft YaHei UI", 9, "bold"), padx=14, pady=5,
                  command=self.save_schedule).pack(side="left", padx=8)
        tk.Button(brow, text="取消定时", bg=c["bg"], fg=c["text"], bd=0,
                  font=("Microsoft YaHei UI", 9), padx=10, pady=5,
                  command=self.cancel_schedule).pack(side="left")
        saved = cp.schedule_load().get("slots") or []
        if saved:
            for s in saved:
                self.add_slot_row(s.get("start", ""), s.get("stop", ""),
                                  s.get("days", "daily"))
        else:
            self.add_slot_row()

        # 今日签到 + 每门课查询
        tcard = tk.Frame(mid, bg=c["card"], highlightbackground=c["border"],
                         highlightthickness=1)
        tcard.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        pad2 = tk.Frame(tcard, bg=c["card"])
        pad2.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(pad2, text="✓  今日已签到", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(anchor="w")
        self.today = tk.Label(pad2, text="(暂无，监控中…)", bg=c["card"],
                              fg=c["muted"], justify="left", wraplength=440)
        self.today.pack(anchor="w", pady=(10, 0))
        tk.Label(pad2, text="📊  每门课查询次数", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(anchor="w", pady=(16, 0))
        self.queries = tk.Label(pad2, text="(监控中…)", bg=c["card"],
                                fg=c["muted"], justify="left", wraplength=440)
        self.queries.pack(anchor="w", pady=(10, 0))

    # ---------------- 课程勾选 ----------------
    _TIME_VALUES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

    def _build_courses(self, parent):
        c = self.c
        card = tk.Frame(parent, bg=c["card"], highlightbackground=c["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(14, 0))
        pad = tk.Frame(card, bg=c["card"])
        pad.pack(fill="x", padx=16, pady=14)
        head = tk.Frame(pad, bg=c["card"])
        head.pack(fill="x")
        tk.Label(head, text="📚  监控课程", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(side="left")
        self.course_summary = tk.Label(head, text="", bg=c["card"], fg=c["muted"],
                                       font=("Microsoft YaHei UI", 9))
        self.course_summary.pack(side="left", padx=12)
        tk.Label(pad, text="勾选 = 自动抓取并签到；取消勾选后该课不再查询，"
                           "改完点「保存勾选」立即生效（无需重启监控）",
                 bg=c["card"], fg=c["muted"], font=("Microsoft YaHei UI", 9),
                 justify="left", wraplength=900).pack(anchor="w", pady=(6, 8))
        self.course_grid = tk.Frame(pad, bg=c["card"])
        self.course_grid.pack(fill="x")
        self.course_vars = {}
        self._courses_drawn = False
        self._course_hint = tk.Label(self.course_grid, text="正在获取课程列表…",
                                     bg=c["card"], fg=c["muted"],
                                     font=("Microsoft YaHei UI", 9))
        self._course_hint.pack(anchor="w")
        row = tk.Frame(pad, bg=c["card"])
        row.pack(fill="x", pady=(10, 0))
        tk.Button(row, text="全选", bg=c["bg"], fg=c["text"], bd=0,
                  font=("Microsoft YaHei UI", 9), padx=12, pady=5,
                  command=lambda: self.set_all_courses(True)).pack(side="left")
        tk.Button(row, text="全不选", bg=c["bg"], fg=c["text"], bd=0,
                  font=("Microsoft YaHei UI", 9), padx=12, pady=5,
                  command=lambda: self.set_all_courses(False)).pack(
                      side="left", padx=8)
        tk.Button(row, text="保存勾选", bg=c["primary"], fg="white", bd=0,
                  font=("Microsoft YaHei UI", 9, "bold"), padx=14, pady=5,
                  command=self.save_courses).pack(side="left")
        self._fill_courses()

    def _fill_courses(self):
        """课程列表到手后（或切换主题重建界面时）画复选框"""
        if not self._courses:
            return
        if self._courses_drawn:
            return
        for w in self.course_grid.winfo_children():
            w.destroy()
        disabled = set(cp.course_filter_load()["disabled"])
        self.course_vars = {}
        for i, course in enumerate(self._courses):
            cid = str(course.get("id"))
            var = tk.BooleanVar(value=cid not in disabled)
            self.course_vars[cid] = var
            tk.Checkbutton(
                self.course_grid, text=course.get("name") or cid, variable=var,
                bg=self.c["card"], fg=self.c["text"], selectcolor=self.c["bg"],
                activebackground=self.c["card"], activeforeground=self.c["text"],
                anchor="w", justify="left", font=("Microsoft YaHei UI", 10),
                bd=0, highlightthickness=0).grid(
                    row=i // 2, column=i % 2, sticky="w", padx=(0, 18), pady=2)
        self.course_grid.columnconfigure(0, weight=1, uniform="c")
        self.course_grid.columnconfigure(1, weight=1, uniform="c")
        self._courses_drawn = True
        self._update_course_summary()

    def _update_course_summary(self):
        total = len(self._courses)
        if not total:
            return
        if self.course_vars:
            n = sum(1 for v in self.course_vars.values() if v.get())
        else:
            n = total - len(cp.course_filter_load()["disabled"])
        self.course_summary.config(text=f"已勾选 {n} / {total} 门")

    def set_all_courses(self, value):
        for v in self.course_vars.values():
            v.set(value)
        self._update_course_summary()

    def save_courses(self):
        if not self.course_vars:
            self._toast("课程列表还没拿到（检查网络/账号后再试）")
            return
        disabled = [cid for cid, v in self.course_vars.items() if not v.get()]
        cp.course_filter_save(disabled)
        self._update_course_summary()
        self._toast(f"已保存：监控 {len(self.course_vars) - len(disabled)} 门，"
                    f"另外 {len(disabled)} 门不再抓取")
        self.refresh()

    # ---------------- 二维码签到（半自动） ----------------
    def _build_qr(self, parent):
        c = self.c
        card = tk.Frame(parent, bg=c["card"], highlightbackground=c["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(14, 0))
        pad = tk.Frame(card, bg=c["card"])
        pad.pack(fill="x", padx=16, pady=14)
        head = tk.Frame(pad, bg=c["card"])
        head.pack(fill="x")
        tk.Label(head, text="🔳  二维码签到", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(side="left")
        self.qr_state = tk.Label(head, text="", bg=c["card"], fg=c["muted"],
                                 font=("Microsoft YaHei UI", 9))
        self.qr_state.pack(side="left", padx=12)
        tk.Label(pad, text="自动签不了：token 只存在于老师屏幕的二维码里，学生端接口不下发"
                           "（实测 GET /api/rollcall/{id}/qr_code 是 404），窗口约 10 分钟。\n"
                           "本程序能做两件事：① 点名一出现就响铃 + 窗口置顶提醒你；"
                           "② 你若拿到了二维码内容（同学发你的链接/文本），粘进来替你提交。",
                 bg=c["card"], fg=c["muted"], justify="left", wraplength=900,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 8))
        row = tk.Frame(pad, bg=c["card"])
        row.pack(fill="x")
        self.qr_text = tk.StringVar()
        tk.Entry(row, textvariable=self.qr_text, font=("Consolas", 10),
                 bg=c["bg"], fg=c["text"], relief="flat", insertbackground=c["text"]
                 ).pack(side="left", fill="x", expand=True, ipady=4)
        tk.Button(row, text="提交二维码", bg=c["primary"], fg="white", bd=0,
                  font=("Microsoft YaHei UI", 9, "bold"), padx=14, pady=5,
                  command=self.submit_qr).pack(side="left", padx=8)
        self.qr_state.config(text="当前没有进行中的二维码点名")

    def _qr_login(self, relogin=False):
        """拿到可用会话（缓存；relogin=True 强制重新登录）"""
        import auto_checkin as ac
        if relogin or self._qr_session is None or self._qr_sid is None:
            self._qr_session, self._qr_sid = ac.do_login()
        return self._qr_session, self._qr_sid

    def submit_qr(self):
        content = self.qr_text.get().strip()
        if not content:
            self._toast("先把二维码里的文本或链接粘贴到输入框")
            return
        self._toast("正在提交二维码…")
        threading.Thread(target=self._submit_qr_work, args=(content,),
                         daemon=True).start()

    def _submit_qr_work(self, content):
        msg = ""
        try:
            import auto_checkin as ac
            session, sid = self._qr_login()
            alerts = cp.run_status().get("qr_alert") or []
            targets = [{"rid": a.get("rid"), "course": a.get("course")}
                       for a in alerts if a.get("rid")]
            if not targets:
                targets = ac.find_active_qr_rollcalls(session, sid)
            if not targets:
                msg = "当前没有进行中的二维码点名（或者已经签上了）"
            else:
                t = targets[0]
                ok, detail = ac.answer_qr(t["rid"], content, session)
                if not ok and "401" in detail:      # 会话过期 → 重登一次再试
                    session, sid = self._qr_login(relogin=True)
                    ok, detail = ac.answer_qr(t["rid"], content, session)
                if ok:
                    try:
                        ac.record_ok("二维码", t.get("course") or "?", detail)
                    except Exception:
                        pass
                msg = ("✅ " if ok else "❌ ") + f"{t.get('course', '?')}：{detail}"
        except Exception as e:
            msg = f"提交失败: {e}"
        self.after(0, lambda: self._toast(msg))
        self.refresh()

    def _handle_qr_alerts(self, alerts):
        """二维码点名出现 → 响铃 + 置顶 + 浮层（每条点名只提醒一次），并显示倒计时"""
        if not alerts:
            self.qr_state.config(text="当前没有进行中的二维码点名",
                                 fg=self.c["muted"])
            if self._qr_title:
                self.title("XMU 畅课自动签到")
                self._qr_title = False
            return
        a = alerts[0]
        left = a.get("left")
        cnt = (f"还剩 {left // 60} 分 {left % 60} 秒"
               if isinstance(left, int) else "进行中")
        course = a.get("course") or "?"
        self.qr_state.config(text=f"⚠ {course}：{cnt}，请用手机扫码",
                             fg=self.c["danger"])
        if not self._qr_title:
            self.title(f"⚠ 二维码签到：{course}（{cnt}）")
            self._qr_title = True
        rid = a.get("rid")
        if rid not in self._qr_notified:
            self._qr_notified.add(rid)
            self._toast(f"⚠ 二维码点名：{course}\n{cnt}，请用手机扫码\n"
                        f"（脚本拿不到 token，无法自动签）")
            try:
                self.bell()
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass
            try:
                self.deiconify()
                self.lift()
                self.attributes("-topmost", True)
                self.after(4000, lambda: self.attributes("-topmost", False))
            except Exception:
                pass

    def _build_log(self, parent):
        # 终端风格实时控制台面板
        self.term_bg = "#0b0f16"
        self.term_fg = "#38e08c"
        card = tk.Frame(parent, bg=self.term_bg, highlightbackground="#20283a",
                        highlightthickness=1)
        card.pack(fill="x", pady=(14, 0))
        self.log_text = tk.Label(
            card, text="", bg=self.term_bg, fg=self.term_fg,
            justify="left", font=("Consolas", 10), anchor="w")
        self.log_text.pack(fill="x", padx=14, pady=12)

    def _build_account(self, parent):
        c = self.c
        card = tk.Frame(parent, bg=c["card"], highlightbackground=c["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(14, 0))
        pad = tk.Frame(card, bg=c["card"])
        pad.pack(fill="x", padx=16, pady=14)
        tk.Label(pad, text="👤  账号设置", bg=c["card"], fg=c["text"],
                 font=self.font_head).pack(anchor="w")
        # 当前账号
        self.account_label = tk.Label(
            pad, text="", bg=c["card"], fg=c["muted"],
            font=("Microsoft YaHei UI", 10), anchor="w")
        self.account_label.pack(fill="x", pady=(10, 0))
        # 输入行
        row = tk.Frame(pad, bg=c["card"])
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="学号", bg=c["card"], fg=c["text"]).pack(side="left")
        self.acct_sid = tk.StringVar()
        tk.Entry(row, width=20, textvariable=self.acct_sid, font=("Consolas", 11),
                 bg=c["bg"], fg=c["text"], relief="flat").pack(side="left",
                                                               padx=(8, 18))
        tk.Label(row, text="密码", bg=c["card"], fg=c["text"]).pack(side="left")
        self.acct_pwd = tk.StringVar()
        tk.Entry(row, width=16, textvariable=self.acct_pwd, show="*",
                 font=("Consolas", 11), bg=c["bg"], fg=c["text"],
                 relief="flat").pack(side="left", padx=8)
        tk.Button(row, text="保存账号", bg=c["primary"], fg="white", bd=0,
                  font=("Microsoft YaHei UI", 9, "bold"), padx=14, pady=5,
                  command=self.save_account).pack(side="left", padx=10)
        tk.Label(pad, text="保存后下次打开自动登录，无需重新输入", bg=c["card"],
                 fg=c["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w",
                                                                     pady=(8, 0))
        self.refresh_account()

    def refresh_account(self):
        sid = cp.current_account()
        if sid:
            self.account_label.config(text=f"当前账号：{sid}")
            self.acct_sid.set(sid)
        else:
            self.account_label.config(text="尚未保存账号")

    def save_account(self):
        msg = cp.save_creds(self.acct_sid.get(), self.acct_pwd.get())
        self.acct_pwd.set("")
        self.refresh_account()
        self._toast(msg)

    # ---------------- 主题 ----------------
    def toggle_theme(self):
        self._dark = not self._dark
        self.c = self.THEMES["dark"] if self._dark else self.THEMES["light"]
        self._configure_styles()
        self.theme_btn.config(text="☀ 浅色" if self._dark else "🌙 深色")
        self.rebuild()

    def rebuild(self):
        for w in self.winfo_children():
            w.destroy()
        self._build_ui()

    # ---------------- 定时行 ----------------
    def add_slot_row(self, start="", stop="", days="daily"):
        c = self.c
        row = tk.Frame(self.slot_container, bg=c["card"])
        sv = tk.StringVar(value=start)
        ev = tk.StringVar(value=stop)
        dv = tk.StringVar(value=cp.DAY_LABELS.get(days, "每天"))
        ttk.Combobox(row, width=6, textvariable=sv, values=self._TIME_VALUES,
                     font=("Consolas", 11)).pack(side="left", padx=(0, 10))
        ttk.Combobox(row, width=6, textvariable=ev, values=self._TIME_VALUES,
                     font=("Consolas", 11)).pack(side="left", padx=(0, 10))
        ttk.Combobox(row, width=6, textvariable=dv, state="readonly",
                     values=list(cp.DAY_LABELS.values()),
                     font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 8))
        tk.Button(row, text="✕", bd=0, bg=c["card"], fg=c["muted"],
                  font=("Microsoft YaHei UI", 9),
                  command=lambda: self.remove_slot_row(row)).pack(side="left",
                                                                  padx=8)
        row.pack(fill="x", pady=4)
        self.slot_rows.append((row, sv, ev, dv))

    def remove_slot_row(self, row):
        self.slot_rows = [r for r in self.slot_rows if r[0] is not row]
        row.destroy()
        if not self.slot_rows:
            self.add_slot_row()
        self._apply_schedule_silent()

    def _collect_slots(self):
        rev = {label: key for key, label in cp.DAY_LABELS.items()}
        return [{"start": sv.get().strip(), "stop": ev.get().strip(),
                 "days": rev.get(dv.get().strip(), "daily")}
                for (_, sv, ev, dv) in self.slot_rows]

    def _apply_schedule_silent(self):
        """删除时段后立即同步到系统（不弹提示）"""
        slots = self._collect_slots()
        def _work():
            try:
                valid = [s for s in slots if s.get("start") or s.get("stop")]
                if valid:
                    cp.schedule_apply(valid)
                else:
                    cp.schedule_cancel()
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def save_schedule(self):
        slots = self._collect_slots()
        self._toast(cp.schedule_apply(slots))
        self.refresh()

    def cancel_schedule(self):
        self._toast(cp.schedule_cancel())
        for (_, sv, ev, _dv) in self.slot_rows:
            sv.set(""); ev.set("")
        self.refresh()

    # ---------------- 启动/停止 ----------------
    def do_start(self):
        # 用户显式启动 → 之后掉线才会自动拉起
        self._armed = True
        self._user_stopped = False
        threading.Thread(target=lambda: self._after(cp.start()),
                         daemon=True).start()

    def do_stop(self):
        # 手动停止后不再被自动拉起（否则 30 秒后又被拉起来，像"停不掉"）
        self._armed = False
        self._user_stopped = True
        threading.Thread(target=lambda: self._after(cp.stop()),
                         daemon=True).start()

    def _after(self, msg):
        self.after(0, lambda: self._toast(msg))
        self.refresh()

    def _toast(self, msg):
        """右上角浮层提示：按内容行数自适应高度，长消息也看得全"""
        msg = (msg or "").strip() or "（无返回）"
        lines = msg.count("\n") + 1
        w = 430
        h = 78 + 19 * min(lines, 12)
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=self.c["card"])
        x = max(8, self.winfo_rootx() + self.winfo_width() - w - 24)
        y = self.winfo_rooty() + 70
        top.geometry(f"{w}x{h}+{x}+{y}")
        tk.Label(top, text=msg, bg=self.c["card"], fg=self.c["text"],
                 justify="left", anchor="w", wraplength=w - 32,
                 font=("Microsoft YaHei UI", 10)).pack(padx=16, pady=12,
                                                       fill="both", expand=True)
        top.after(3400, top.destroy)

    # ---------------- 状态横幅绘制 ----------------
    def _paint_hero(self):
        c = self.c
        cv = self.hero
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w <= 1:
            return
        running = self._hero_running
        color = c["ok"] if running else c["muted"]
        # 呼吸灯
        if running:
            self._breath = (self._breath + 1) % 360
            import math
            r = 14 + int(4 * (0.5 + 0.5 * math.sin(math.radians(self._breath))))
        else:
            r = 9
        cv.create_oval(34, h // 2 - r, 34 + 2 * r, h // 2 + r,
                       fill=color, outline="")
        title = self._hero_title or "未运行"
        sub = self._hero_sub or "点击「启动 / 重启」开始监控"
        cv.create_text(34 + 2 * r + 18, h // 2 - 14, text=title,
                       anchor="w", fill=c["text"],
                       font=("Microsoft YaHei UI", 16, "bold"))
        cv.create_text(34 + 2 * r + 18, h // 2 + 16, text=sub,
                       anchor="w", fill=c["muted"],
                       font=("Microsoft YaHei UI", 10))

    _hero_title = "未运行"
    _hero_sub = "点击「启动 / 重启」开始监控"

    def _update_hero(self, running, title, sub):
        self._hero_running = running
        self._hero_title = title
        self._hero_sub = sub

    def _running_elapsed(self):
        try:
            import time
            start = float(cp.START_FILE.read_text().strip())
            sec = int(time.time() - start)
            if sec < 0:
                return ""
            h = sec // 3600; m = (sec % 3600) // 60
            return f"{h}小时{m}分" if h else f"{m}分钟"
        except Exception:
            return ""

    # ---------------- 数据刷新 ----------------
    def auto_refresh(self):
        self.refresh()
        self.after(REFRESH_MS, self.auto_refresh)

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            if not self._courses and self._may_probe_courses():
                try:
                    import auto_checkin as ac
                    s, sid = ac.do_login()
                    self._courses = [
                        {"id": c.get("id"),
                         "name": c.get("display_name") or c.get("name")
                         or str(c.get("id"))}
                        for c in ac.get_courses(s)]
                    self._course_count = len(self._courses)
                except Exception:
                    pass
            monitored = (cp.course_monitor_count(self._courses)[0]
                         if self._courses else 0)
            data = {
                "running": cp.find_running(), "today": cp.today_done(),
                "events": cp.tail_events(8), "sched": cp.schedule_load(),
                "course_count": monitored,
                "all_total": len(cp.load_json_file(cp.RECORD_FILE, [])),
                "launches": cp.launch_count(), "run_status": cp.run_status(),
            }
        except Exception as e:
            data = {"error": str(e)}
        try:
            self.after(0, self._render, data)
        finally:
            self._busy = False

    def _may_probe_courses(self):
        """课程列表探测限流：登录失败时不要每 3 秒打一次 CAS 登录接口"""
        now = time.time()
        if now - getattr(self, "_last_probe", 0.0) < 60:
            return False
        self._last_probe = now
        return True

    def _ensure_alive(self, running):
        """签到子进程掉线时自动拉起（每 30 秒最多检查一次）。

        三个前提，缺一不可：
        1. 用户自己点过「启动 / 重启」（_armed）—— 打开软件不会自动开始签到；
        2. 没点过「停止」（_user_stopped）；
        3. 当前在定时时段内（否则定时关闭到点停了，30 秒后又被拉起来）。
        """
        if running or not self._armed or self._user_stopped:
            return
        if not cp.schedule_active_now():
            return
        now = time.time()
        if now - getattr(self, "_last_ensure", 0.0) < 30:
            return
        self._last_ensure = now
        self.ensure_running()

    def _render(self, data):
        if "error" in data:
            self._update_hero(False, "读取失败", "检查网络后重试")
            self._paint_hero()
            return
        running = bool(data["running"])
        self._last_pid = data["running"]
        if running and not self._user_stopped:
            # 发现监控已经在跑（定时任务 / 命令行 / 上次手动启动的）→ 允许它掉线后
            # 被自动拉起；但打开软件本身绝不会去启动一个没在跑的监控
            # （_armed 初始为 False，只有用户点「启动」或这种情况才会置位）
            self._armed = True
        self._fill_courses()
        self._ensure_alive(running)

        # 统计
        today_total = sum(info["n"] for info in data["today"].values())
        rs = data["run_status"]
        vals = [today_total, data["all_total"], data["course_count"],
                data["launches"], rs.get("restart_count", 0),
                rs.get("query_total", 0)]
        for i, v in enumerate(vals):
            self.stat_num[i].config(text=str(v))

        if running:
            elapsed = self._running_elapsed()
            sub = f"已运行 {elapsed}    ·    PID {data['running']}" if elapsed \
                else f"PID {data['running']}"
            self._update_hero(True, "正在监控签到", sub)
        else:
            nxt, kind = cp.schedule_next_change()
            if self._user_stopped:
                self._update_hero(False, "已手动停止", "点「启动 / 重启」恢复监控")
            elif not cp.schedule_active_now():
                tip = (f"下次 {nxt.strftime('%m-%d %H:%M')} 自动启动"
                       if nxt else "等待定时时段")
                self._update_hero(False, "定时待机中", f"当前不在定时时段 · {tip}")
            elif not self._armed:
                self._update_hero(False, "未运行",
                                  "点「启动 / 重启」开始监控（软件不会自动开始）")
            else:
                self._update_hero(False, "未运行", "点击「启动 / 重启」开始监控")
        self._paint_hero()

        # 定时
        self.sched.config(text="定时：" + cp.schedule_status_text())

        # 二维码点名提醒（响铃 + 置顶 + 倒计时）
        self._handle_qr_alerts(rs.get("qr_alert") or [])

        # 今日签到
        today = data["today"]
        if today:
            lines = []
            for name, info in today.items():
                types = "、".join(f"{t}×{n}" for t, n in info["types"].items())
                lines.append(f"• {name}（{types}）")
            self.today.config(text="\n".join(lines), fg=self.c["text"])
        else:
            self.today.config(text="(暂无，监控中…)", fg=self.c["muted"])

        # 每门课查询
        cq = rs.get("course_queries") or {}
        if cq:
            ql = [f"• {n}: {v} 次" for n, v in cq.items()]
            self.queries.config(text="\n".join(ql), fg=self.c["text"])
        else:
            self.queries.config(text="(监控中…)", fg=self.c["muted"])

        # 终端实时控制台
        self.log_text.config(text=self._build_terminal(data, rs))

    def _build_terminal(self, data, rs):
        import time as _t
        line = "=" * 48
        now = _t.strftime("%H:%M:%S")
        try:
            sec = int(_t.time() - float(cp.START_FILE.read_text().strip()))
            if sec < 0:
                sec = 0
            run_txt = f"{sec // 60}m{sec % 60}s"
        except Exception:
            run_txt = "-"
        course_n = data.get("course_count", 0)
        query_n = rs.get("query_total", 0)
        ev = rs.get("events") or data.get("events") or []
        ev_txt = "\n".join(ev[-12:]) if ev else "(等待签到事件…)"
        head = "XMU Tronclass 全自动签到"
        rows = [
            head.center(50),
            line,
            f"时间: {now}    运行: {run_txt}",
            f"课程: {course_n} 门    查询: {query_n} 次",
            "GPS : 自动三角定位",
            "门控: 已签>25% 或 >20 人先签",
            line,
            ev_txt,
            line,
            "实时监控中（点「停止」结束）",
        ]
        return "\n".join(rows)


def _redirect_log():
    """打包成 windowed exe 后没有控制台：sys.stdout 要么是 None，要么是父进程
    重定向过来的块缓冲句柄（日志长时间空白）。统一改成行缓冲写 checkin.log，
    否则登录失败/崩溃都无从排查；确实挂着控制台时保持原样。"""
    try:
        if ctypes.windll.kernel32.GetConsoleWindow():
            return
    except Exception:
        pass
    try:
        from pathlib import Path
        base = (Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent)
        f = open(base / "checkin.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv:
        cmd = argv[0].lstrip("-").lower()
        if cmd == "checkin":
            _redirect_log()
            import auto_checkin
            auto_checkin.main()
        elif cmd == "start":
            print(cp.start())
        elif cmd == "stop":
            print(cp.stop())
        elif cmd == "schedule":
            # 按 schedule.json 重新注册计划任务（任务动作指向本 exe）
            slots = (cp.schedule_load().get("slots") or []) if \
                cp.schedule_load().get("enabled") else []
            print(cp.schedule_apply(slots) if slots else cp.schedule_cancel())
        elif cmd == "qr":
            # 二维码半自动：把二维码内容贴进来，自动找当前二维码点名并提交
            import auto_checkin as ac
            content = " ".join(argv[1:]).strip()
            if not content:
                print("用法: XMU畅课签到.exe qr <二维码内容或链接>")
            else:
                s, sid = ac.do_login()
                targets = ac.find_active_qr_rollcalls(s, sid)
                if not targets:
                    print("当前没有进行中的二维码点名（或已签上）")
                else:
                    t = targets[0]
                    ok, detail = ac.answer_qr(t["rid"], content, s)
                    print(("OK  " if ok else "FAIL ") + f"{t['course']}: {detail}")
                    if ok:
                        ac.record_ok("二维码", t["course"], detail)
        else:
            App().mainloop()
    else:
        App().mainloop()
