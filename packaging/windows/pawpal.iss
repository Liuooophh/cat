#ifndef AppVersion
  #define AppVersion "6.11"
#endif
#ifndef PayloadDir
  #error PayloadDir must point to the built onedir package
#endif
#ifndef PrerequisiteDir
  #error PrerequisiteDir must point to verified signed installers
#endif
#define RootDir SourcePath + "..\.."

[Setup]
AppId={{829BB835-08CE-4EE1-926B-1E9D9328F6D4}
AppName=猫咪桌宠
AppVersion={#AppVersion}
AppVerName=猫咪桌宠 {#AppVersion}
AppPublisher=Pawpal Cat
DefaultDirName={localappdata}\Programs\PawpalCat
DefaultGroupName=猫咪桌宠
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
OutputDir={#RootDir}\dist
OutputBaseFilename=PawpalCat-v{#AppVersion}-Setup
SetupIconFile={#RootDir}\pet_icon.ico
UninstallDisplayIcon={app}\PawpalCat.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
Uninstallable=yes
SetupLogging=yes
AppMutex={code:GetAppMutex}

[Languages]
Name: "zhcn"; MessagesFile: "{#SourcePath}ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
Name: "pawnio"; Description: "安装 PawnIO 温度检测驱动（需要管理员授权）"; GroupDescription: "温度组件（可选）："; Flags: unchecked; Check: not PawnIOInstalled
Name: "netfx"; Description: "补装 .NET Framework 4.8（需要联网及管理员授权）"; GroupDescription: "温度组件（可选）："; Flags: unchecked; Check: not FrameworkInstalled

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}pawpal-installed.marker"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PrerequisiteDir}\PawnIO_setup.exe"; DestDir: "{app}\Prerequisites"; Flags: ignoreversion
Source: "{#PrerequisiteDir}\ndp48-web.exe"; DestDir: "{app}\Prerequisites"; Flags: ignoreversion
Source: "{#SourcePath}THIRD-PARTY-NOTICES.txt"; DestDir: "{app}\Prerequisites"; Flags: ignoreversion
Source: "{#SourcePath}prerequisites.lock.json"; DestDir: "{app}\Prerequisites"; Flags: ignoreversion
Source: "{#RootDir}\docs\INSTALLATION.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#RootDir}\docs\SPEECH.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "{#RootDir}\docs\TEMPERATURE.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\猫咪桌宠"; Filename: "{app}\PawpalCat.exe"
Name: "{group}\安装与温度组件说明"; Filename: "{app}\docs\INSTALLATION.md"
Name: "{autodesktop}\猫咪桌宠"; Filename: "{app}\PawpalCat.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PawpalCat.exe"; Description: "启动猫咪桌宠"; Flags: nowait postinstall skipifsilent

[Code]
var
  PrerequisiteRestart: Boolean;

function FrameworkInstalled: Boolean;
begin
  Result := IsDotNetInstalled(net48, 0);
end;

function PawnIOInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO') or
            RegKeyExists(HKLM32, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO');
end;

function QAMode: Boolean;
begin
  Result := ExpandConstant('{param:QA|0}') = '1';
end;

function GetAppMutex(Param: String): String;
begin
  if QAMode then
    Result := 'Local\PawpalCat-Installer-Isolated-QA'
  else
    Result := 'Local\PawpalCat-' + Copy(Lowercase(GetSHA256OfString(
      Utf8Encode(GetEnv('USERDOMAIN') + '\' + GetEnv('USERNAME')))), 1, 16);
end;

procedure RunPrerequisite(const FileName, Parameters, DisplayName: String);
var
  ExitCode: Integer;
begin
  if WizardSilent or QAMode then
  begin
    Log('Skipped prerequisite in silent/QA mode: ' + DisplayName);
    exit;
  end;
  if not ShellExec('runas', ExpandConstant('{app}\Prerequisites\') + FileName,
                   Parameters, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) then
  begin
    Log('Prerequisite authorization/start failed: ' + DisplayName + ' ' + IntToStr(ExitCode));
    MsgBox(DisplayName + ' 未完成安装。桌宠仍可使用，温度可能暂不可用；可稍后从安装目录的 Prerequisites 文件夹安装。', mbInformation, MB_OK);
  end
  else if (ExitCode = 3010) or (ExitCode = 1641) then
    PrerequisiteRestart := True
  else if ExitCode <> 0 then
  begin
    Log('Prerequisite returned ' + IntToStr(ExitCode) + ': ' + DisplayName);
    MsgBox(DisplayName + ' 返回代码 ' + IntToStr(ExitCode) + '，温度可能暂不可用。请查看组件安装提示。', mbInformation, MB_OK);
  end;
end;

procedure UpdateExistingStartup;
var
  Current, OldRoot: String;
begin
  if QAMode then exit;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PawpalCat', Current) then
  begin
    OldRoot := Lowercase(ExpandConstant('{localappdata}\PawpalCat\app\'));
    if (Pos('"' + OldRoot, Lowercase(Current)) = 1) or
       (CompareText(Current, '"' + ExpandConstant('{app}\PawpalCat.exe') + '" --autostart') = 0) then
      RegWriteStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run',
                          'PawpalCat', '"' + ExpandConstant('{app}\PawpalCat.exe') + '" --autostart');
  end;
end;

function InitializeSetup: Boolean;
begin
  Log('Framework48Installed=' + IntToStr(Ord(FrameworkInstalled)));
  Log('PawnIOInstalled=' + IntToStr(Ord(PawnIOInstalled)));
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not FrameworkInstalled and WizardIsTaskSelected('netfx') then
      RunPrerequisite('ndp48-web.exe', '/norestart', '.NET Framework 4.8');
    if not PawnIOInstalled and WizardIsTaskSelected('pawnio') then
      RunPrerequisite('PawnIO_setup.exe', '-install', 'PawnIO');
    UpdateExistingStartup;
  end;
end;

function NeedRestart: Boolean;
begin
  Result := PrerequisiteRestart;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Current: String;
begin
  if (CurUninstallStep = usUninstall) and not QAMode then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PawpalCat', Current) then
      if CompareText(Current, '"' + ExpandConstant('{app}\PawpalCat.exe') + '" --autostart') = 0 then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PawpalCat');
  // PawnIO, .NET, user settings and Windows credentials belong to the user.
  // No uninstall deletion is issued for these shared/persistent resources.
end;
