# -*- coding: utf-8 -*-
"""一键构建：图标 → exe（PyInstaller）→ 安装包（Inno Setup）。

用法:
    python tools/build_installer.py                # 全流程
    python tools/build_installer.py --skip-exe     # 只用现有 dist 里的 exe 打安装包
    python tools/build_installer.py --skip-icon    # 不重画图标

依赖:
    pip install pyinstaller        （打包 exe）
    Pillow                         （生成图标，可跳过）
    Inno Setup 6                   （编译安装包；ISCC.exe）
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK，打印中文/符号可能抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXE_NAME = "XMU畅课签到.exe"
EXE = DIST / EXE_NAME
ISS = ROOT / "installer" / "XMU-AC.iss"

ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    # winget 按用户安装时落在这里
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
]


def find_iscc():
    for c in ISCC_CANDIDATES:
        if c.exists():
            return c
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    # 最后再扫一遍常见安装根目录
    for root in (os.environ.get("LOCALAPPDATA", ""),
                 r"C:\Program Files", r"C:\Program Files (x86)"):
        base = Path(root) / "Programs" if root.endswith("LocalAppData") else Path(root)
        for pat in ("Inno Setup 6/ISCC.exe", "Inno Setup*/ISCC.exe"):
            for hit in base.glob(pat):
                if hit.exists():
                    return hit
    sys.exit("找不到 ISCC.exe：请先安装 Inno Setup 6\n"
             "  winget install --id JRSoftware.InnoSetup --exact")


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0:
        sys.exit(f"命令失败（退出码 {r.returncode}）: {cmd[0]}")
    return r


def read_version():
    f = ROOT / "VERSION"
    v = f.read_text(encoding="utf-8").strip() if f.exists() else "0.0.0"
    return v or "0.0.0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-exe", action="store_true", help="跳过 PyInstaller")
    ap.add_argument("--skip-icon", action="store_true", help="跳过图标生成")
    ap.add_argument("--clean", action="store_true", help="PyInstaller 用 --clean")
    args = ap.parse_args()

    version = read_version()
    print(f"=== XMU 畅课签到 打包 v{version} ===")

    if not args.skip_icon:
        try:
            import PIL  # noqa: F401
            run([sys.executable, str(ROOT / "tools" / "make_icon.py")])
        except ImportError:
            print("! 未安装 Pillow，跳过图标生成（沿用现有 assets/app.ico）")

    if not args.skip_exe:
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
        if args.clean:
            cmd.append("--clean")
        cmd.append("XMU畅课签到.spec")
        run(cmd, cwd=ROOT)

    if not EXE.exists():
        sys.exit(f"找不到 {EXE}，请先构建 exe（去掉 --skip-exe）")
    print(f"exe: {EXE}  ({EXE.stat().st_size / 1048576:.2f} MB)")

    iscc = find_iscc()
    out_dir = DIST
    run([iscc, f"/DAppVersion={version}", str(ISS)], cwd=ISS.parent)

    setup = out_dir / f"XMU-AC-CHECKIN-Setup-{version}.exe"
    if not setup.exists():
        # ISCC 的 OutputDir 相对 .iss 文件解析，两种位置都找一下
        alt = ISS.parent / ".." / "dist" / setup.name
        if alt.exists():
            setup = alt.resolve()
    if not setup.exists():
        sys.exit("安装包似乎没有生成，请检查 ISCC 输出")
    print(f"\n[OK] 安装包: {setup}  ({setup.stat().st_size / 1048576:.2f} MB)")


if __name__ == "__main__":
    main()
