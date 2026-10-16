# 安装包（Inno Setup）

把 `dist\XMU畅课签到.exe` 打成标准 Windows 安装包：中文安装向导、开始菜单/桌面快捷方式、
带卸载器，**覆盖安装与卸载都不丢配置**。

## 一键构建

```powershell
pip install -r requirements.txt pyinstaller Pillow
winget install --id JRSoftware.InnoSetup --exact     # 只需一次

python tools/build_installer.py
# 产物：
#   dist\XMU畅课签到.exe                    单文件程序（PyInstaller）
#   dist\XMU-AC-CHECKIN-Setup-<版本>.exe     安装包（Inno Setup）
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--skip-exe` | 不重新跑 PyInstaller，用现有 `dist\XMU畅课签到.exe` 直接打安装包 |
| `--skip-icon` | 不重画图标（沿用 `assets/app.ico`） |
| `--clean` | PyInstaller 加 `--clean`，彻底重建 |

版本号写在仓库根目录 `VERSION`，构建时通过 `/DAppVersion=` 传给 Inno。

## 安装包做了什么

| 项目 | 值 / 行为 |
|---|---|
| 安装范围 | **每用户**（`PrivilegesRequired=lowest`），不需要管理员、不弹 UAC，可静默安装 |
| 默认安装目录 | `%LOCALAPPDATA%\Programs\XMU-AC-CHECKIN` |
| 用户数据目录 | `%APPDATA%\XMU-AC-CHECKIN`（**不在安装目录**，所以覆盖安装/卸载都不会丢） |
| 快捷方式 | 开始菜单（程序 / 使用说明 / 卸载）+ 可选桌面图标 + 可选开机自启 |
| 安装前 | 自动 `taskkill` 掉正在运行的签到程序，避免文件被占用导致失败 |
| 卸载时 | 询问"是否同时删除配置与签到记录"，默认保留 |
| 语言 | 简体中文 + English（中文语言文件随仓库携带，见下） |

数据目录解析逻辑在 [`paths.py`](../paths.py)，有三条规则（有单元测试
[`tests/test_paths.py`](../tests/test_paths.py) 覆盖）：

1. 环境变量 `XMU_DATA_DIR` 优先；
2. **程序目录里已有老数据**（creds.env / checkin_record.json … 任一）→ 继续用程序目录，
   所以"把 exe 放哪、数据就在哪"的便携用法和个人已有部署不受影响；
3. 否则用 `%APPDATA%\XMU-AC-CHECKIN`（安装版默认）。

## 静默安装 / 卸载（脚本化部署）

```powershell
# 装到默认位置
.\XMU-AC-CHECKIN-Setup-1.5.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

# 指定目录、不要快捷方式、写日志
.\XMU-AC-CHECKIN-Setup-1.5.0.exe /VERYSILENT /NORESTART /NOICONS `
    /DIR="D:\Apps\XMU-AC-CHECKIN" /LOG="%TEMP%\xmu_install.log"
```

卸载：

```powershell
& "$env:LOCALAPPDATA\Programs\XMU-AC-CHECKIN\unins000.exe" /VERYSILENT
```

## 中文语言文件

Inno Setup 官方安装包只带 25 种语言（不含简体中文），中文 MessagesFile 放在
`installer/languages/ChineseSimplified.isl`（来自社区翻译
[kira-96/Inno-Setup-Chinese-Simplified-Translation](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)，
面向 Inno Setup 6.5.0+）。仓库里自带一份副本，构建时不依赖网络。

## 注意：构建/测试环境的坑

打包本身与这些无关，但如果你在**受限沙箱**里跑（比如自动化 agent 会话），
安装程序可能报：

```
Creating directory: ...\Start Menu\Programs\XMU 畅课自动签到
  错误 5: 拒绝访问。
IPersistFile::Save 失败；错误代码 0x80070005。
RegCreateKeyEx 失败；错误代码 5。
```

原因是进程被降到了 **Low 完整性级别**（沙箱策略），而开始菜单与 `HKCU` 写入需要
Medium。诊断方法：

```powershell
# 看安装程序子进程的完整性级别（TokenIntegrityLevel，RID 8192=Medium / 4096=Low）
# 正常桌面会话里双击安装不会遇到
```

另外 `%TEMP%` 上如果残留了别的沙箱写入的拒绝 ACE，也会让安装程序建不了
`is-XXXX.tmp` 临时目录；此时可用 `/DIR=` 指定目录 + 把 `TEMP`/`TMP` 指到可写路径再试。

真实用户（双击安装向导）不受上述限制。
