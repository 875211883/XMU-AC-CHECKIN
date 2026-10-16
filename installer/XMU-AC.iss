; ============================================================================
;  XMU 畅课自动签到 —— Inno Setup 安装包脚本
;
;  特点：
;   * 每用户安装（%LOCALAPPDATA%\Programs），不需要管理员权限、不弹 UAC
;   * 中文安装向导（含英文回退）
;   * 桌面/开始菜单快捷方式 + 卸载器（含"删除用户数据"选项）
;   * 安装/卸载时自动停掉正在运行的签到进程，避免文件占用导致失败
;   * **覆盖安装不丢配置**：用户数据（creds.env / course_filter.json / 签到记录 /
;     日志）不在安装目录，默认位于 %APPDATA%\XMU-AC-CHECKIN；程序优先用
;     启动目录（安装目录）里的数据，所以老用户把数据放安装目录也能继续用
;
;  构建：python tools/build_installer.py    （或 ISCC.exe installer\XMU-AC.iss）
; ============================================================================

#define AppName        "XMU 畅课自动签到"
#define AppNameEn      "XMU Tronclass Auto Check-in"
#define AppExe         "XMU畅课签到.exe"
#define AppPublisher   "875211883"
#define AppURL         "https://github.com/875211883/XMU-AC-CHECKIN"
#define DataDirName    "XMU-AC-CHECKIN"

#ifndef AppVersion
  #define AppVersion "1.5.0"
#endif
#ifndef SourceExe
  #define SourceExe "..\dist\XMU畅课签到.exe"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{8E31C4A7-5B2E-4E77-9E4B-2C6A1F0D7A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\XMU-AC-CHECKIN
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
; 注意：不要开 PrivilegesRequiredOverridesAllowed=dialog。
; 它在某些受限/沙箱环境下会让安装程序走"需要提权"的判断分支，
; 表现为写开始菜单与 HKCU 卸载键时报 Error 5 拒绝访问。
AllowUNCPath=no
LicenseFile=..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=XMU-AC-CHECKIN-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=no
RestartApplications=no
; 程序目录下的用户数据（老用法：把 exe 放哪数据就在哪）不能被卸载器误删
Uninstallable=yes

[Languages]
; 简体中文语言文件来自社区翻译（Inno Setup 6.5.0+）：
; https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation
; 仓库内自带一份副本，避免构建时依赖网络。
Name: "chinese"; MessagesFile: "compiler:Default.isl,languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.WelcomeLabel2=即将安装 [name/ver] 到你的电脑。%n%n本程序是你自己账号的签到助手：支持数字签到、GPS 签到、二维码提醒，带签到门控、课程勾选与定时开关。%n%n安装到你自己的用户目录，不需要管理员权限。
chinese.KeepData=保留我的配置与签到记录（推荐）
chinese.KeepDataHint=配置与记录不在安装目录，覆盖安装/卸载都不会被删除。勾掉则连配置一起清掉。
chinese.AutoStart=开机自动启动（登录后最小化到后台监控）
chinese.CreateDesktop=创建桌面快捷方式
chinese.KillRunning=安装前先关闭正在运行的签到程序：
chinese.RunAfter=运行 {#AppName}
chinese.DataNote=你的配置（学号密码、课程勾选、签到记录）保存在：%n%1
chinese.OldExeNote=检测到旧版本安装在：%n%1%n安装完成后建议把旧目录里的 creds.env、course_filter.json、checkin_record.json 复制到新目录（或在设置里重填账号）。
english.WelcomeLabel2=This will install [name/ver] on your computer.%n%nA check-in helper for your own Tronclass account: number check-in, GPS check-in, QR reminder, with gating, course selection and scheduler.%n%nIt installs into your user profile; no administrator rights required.
english.KeepData=Keep my settings and check-in records (recommended)
english.KeepDataHint=Settings live outside the install folder, so upgrades and uninstall keep them. Uncheck to remove them too.
english.AutoStart=Start automatically at sign-in (minimized)
english.CreateDesktop=Create a desktop shortcut
english.KillRunning=Closing the running check-in program first:
english.RunAfter=Run {#AppName}
english.DataNote=Your settings (account, course selection, records) are stored in:%n%1
english.OldExeNote=An older copy was found at:%n%1%nAfter installing, copy creds.env, course_filter.json and checkin_record.json from there if you want to keep using them.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktop}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart";   Description: "{cm:AutoStart}";     GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "keepdata";    Description: "{cm:KeepData}";      GroupDescription: "{cm:KeepDataHint}"

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\LICENSE";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\教学.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\creds.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\使用说明（README）"; Filename: "{app}\README.md"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon
; 开机自启：用「启动」文件夹而不是注册表 Run，方便用户自己看/删
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:RunAfter}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\docs\教学.md"; Description: "打开教学文档"; Flags: shellexec nowait postinstall skipifsilent unchecked

[UninstallDelete]
; 只删安装目录里的程序文件与打包资源；用户数据默认保留（在 %APPDATA% 或老目录里）
Type: filesandordirs; Name: "{app}\docs"
Type: files;          Name: "{app}\README.md"
Type: files;          Name: "{app}\LICENSE"
Type: files;          Name: "{app}\creds.env.example"
Type: files;          Name: "{app}\app.ico"

[Code]
const
  WM_CLOSE = $0010;

{ 停掉正在运行的签到程序：否则覆盖安装时 exe 被占用会失败 }
procedure StopRunningApp();
var
  R: Integer;
begin
  if Exec('taskkill.exe', '/IM "{#AppExe}" /T /F', '',
          SW_HIDE, ewWaitUntilTerminated, R) then
    Sleep(600);
end;

function InitializeSetup(): Boolean;
var
  Old: String;
begin
  Result := True;
  { 先停旧进程，避免"文件被占用"导致安装失败 }
  StopRunningApp();

  { 提示：如果别处还留着一份 exe（比如 D:\签到\XMU畅课签到），避免装成两份 }
  Old := '';
  if FileExists(ExpandConstant('{sd}\签到\XMU畅课签到\{#AppExe}')) then
    Old := ExpandConstant('{sd}\签到\XMU畅课签到');
  if Old <> '' then
    if MsgBox(FmtMessage(CustomMessage('OldExeNote'), [Old]) + #13#10#13#10 +
              '是否继续安装？（继续 = 安装到新目录，旧目录保留不动）',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  { 卸载前先停进程，否则卸载器删不掉正在运行的 exe }
  StopRunningApp();

  if MsgBox('是否同时删除配置与签到记录？' + #13#10 +
            '（学号密码、课程勾选、签到记录、日志）' + #13#10#13#10 +
            '选「否」= 只卸载程序，配置保留，以后重装还能直接用。',
            mbConfirmation, MB_YESNO) = IDYES then
  begin
    DelTree(ExpandConstant('{userappdata}\{#DataDirName}'), True, True, True);
    { 顺手清掉老式"数据放安装目录"的残留（程序目录本身会被卸载器删） }
    DeleteFile(ExpandConstant('{app}\creds.env'));
    DeleteFile(ExpandConstant('{app}\course_filter.json'));
    DeleteFile(ExpandConstant('{app}\checkin_record.json'));
    DeleteFile(ExpandConstant('{app}\schedule.json'));
    DeleteFile(ExpandConstant('{app}\auto_checkin_session.json'));
    DeleteFile(ExpandConstant('{app}\auto_checkin_status.json'));
    DeleteFile(ExpandConstant('{app}\code_missing_diag.json'));
    DeleteFile(ExpandConstant('{app}\launch_count.json'));
    DeleteFile(ExpandConstant('{app}\checkin.log'));
    DeleteFile(ExpandConstant('{app}\auto_checkin.pid'));
    DeleteFile(ExpandConstant('{app}\start_time.txt'));
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Note: String;
begin
  if CurStep = ssPostInstall then
  begin
    { 建好数据目录，并留一张说明卡，用户知道配置在哪、怎么迁移 }
    ForceDirectories(ExpandConstant('{userappdata}\{#DataDirName}'));
    Note :=
      'XMU 畅课自动签到 —— 数据目录说明' + #13#10 +
      '=====================================' + #13#10 + #13#10 +
      '你的配置与记录默认保存在：' + #13#10 +
      ExpandConstant('{userappdata}\{#DataDirName}') + #13#10 + #13#10 +
      '里面包括：creds.env（学号密码）、course_filter.json（课程勾选）、' +
      'checkin_record.json（签到记录）、checkin.log（运行日志）。' + #13#10 + #13#10 +
      '因为数据不在安装目录，所以：' + #13#10 +
      '  * 覆盖安装 / 升级  → 配置自动保留' + #13#10 +
      '  * 卸载程序        → 会问你要不要一起删配置' + #13#10 + #13#10 +
      '如果你以前是"把 exe 放哪、数据就在哪"的用法，程序会优先使用' + #13#10 +
      'exe 同目录里的老数据，不需要迁移。' + #13#10 + #13#10 +
      '也可以用环境变量 XMU_DATA_DIR 指定数据目录。' + #13#10;
    SaveStringToFile(ExpandConstant('{userappdata}\{#DataDirName}\在哪里改配置.txt'),
                     Note, False);
  end;
end;
