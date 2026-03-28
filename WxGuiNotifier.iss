; Inno Setup 安装脚本
; 用于创建 WxGuiNotifier 的专业安装包

#define MyAppName "WxGuiNotifier"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Your Company"
#define MyAppExeName "WxGuiNotifier.exe"
#define MyAppURL "https://github.com/yourusername/WxGuiNotifier"

[Setup]
; 基本设置
AppId={{A3B5C7D9-1234-5678-90AB-CDEF12345678}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=
OutputDir=installer_output
OutputBaseFilename=WxGuiNotifier_Setup_{#MyAppVersion}
SetupIconFile=src\img\WeChat.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

; 最低系统要求
MinVersion=10.0.14393

; 用户权限
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; 主程序
Source: "dist\WxGuiNotifier.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "打包修复说明.txt"; DestDir: "{app}"; Flags: ignoreversion

; 注意：不要包含以下文件（在 [Code] 中动态创建）
; Source: "gui_config.json"; DestDir: "{app}"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
; 启动程序
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// 检查 .NET Framework 版本
function CheckDotNetFramework: Boolean;
var
  InstalledVersion: Cardinal;
begin
  // 检查 .NET Framework 4.7.2 或更高版本
  if RegQueryDWordValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', InstalledVersion) then
  begin
    // 4.7.2 = 461808, 4.8 = 528040
    Result := (InstalledVersion >= 461808);
    if not Result then
      Log('.NET Framework version too old: ' + IntToStr(InstalledVersion));
  end
  else
  begin
    Result := False;
    Log('.NET Framework 4.7.2+ not found');
  end;
end;

// 安装前检查
function InitializeSetup(): Boolean;
begin
  Result := True;
  
  // 检查 .NET Framework（警告但不阻止）
  if not CheckDotNetFramework then
  begin
    if MsgBox('未检测到 .NET Framework 4.7.2 或更高版本，程序可能无法运行。是否继续安装？', mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

// 安装后处理
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // 创建初始配置文件目录
    if not DirExists(ExpandConstant('{userappdata}\WxGuiNotifier')) then
    begin
      CreateDir(ExpandConstant('{userappdata}\WxGuiNotifier'));
      Log('Created config directory');
    end;
  end;
end;

// 卸载前检查
function InitializeUninstall(): Boolean;
begin
  Result := True;
  
  if MsgBox('确定要卸载 {#MyAppName} 吗？', mbConfirmation, MB_YESNO) = IDNO then
    Result := False;
end;
