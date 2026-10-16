# -*- mode: python ; coding: utf-8 -*-
import os
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['auto_checkin', 'control_panel', 'paths', 'xmulogin',
                 'unicodedata']
hiddenimports += collect_submodules('xmulogin')

# unicodedata 是扩展模块，某些 Python 安装下 PyInstaller 抓不到，显式带上。
# 用 sysconfig 定位，避免把本机绝对路径写进仓库。
_uni = os.path.join(sysconfig.get_paths()['stdlib'], '..', 'DLLs',
                    'unicodedata.pyd')
binaries = [(_uni, '.')] if os.path.exists(_uni) else []

# 应用图标（由 tools/make_icon.py 生成）。缺失时退化为默认图标，不让打包失败。
_ico = Path(SPECPATH) / 'assets' / 'app.ico'
icon = str(_ico) if _ico.exists() else None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='XMU畅课签到',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
