; Laden Ops Windows installer. Built by windows/build.sh.

#define MyAppName "Laden Ops"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Laden AS"
#define MyAppURL "https://laden.no"
#define MyAppExeName "LadenOps.exe"
#define MyAppId "{{8F4E2A91-6C3D-4B7E-9A15-2D8C0F6E4B17}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) Laden AS
VersionInfoCompany=Laden AS
VersionInfoCopyright=Copyright (C) Laden AS
VersionInfoDescription=Ldash personal dashboard
VersionInfoProductName=Ldash
VersionInfoProductVersion=1.0.0
DefaultDirName={autopf}\Laden Ops
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Ldash-1.0-Windows-Setup
SetupIconFile=laden.ico
UninstallDisplayIcon={app}\laden.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
MinVersion=10.0
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "autostart"; Description: "Start Laden Ops when I sign in"; GroupDescription: "Startup:"; Flags: checkedonce

[Files]
Source: "build\payload\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\laden.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\laden.ico"; Tasks: desktopicon
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Laden Ops"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\redist\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing the Microsoft WebView2 runtime…"; Flags: waituntilterminated; Check: not WebView2Installed
Filename: "{app}\{#MyAppExeName}"; Description: "Start Laden Ops"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=Ldash 1.0 installs Laden Ops, published by Laden AS.%n%nThe first time you open it, you create your account: email, a password that is also the vault password, a username, and the programs on the dashboard. That creates your folder for the config and the vault. Save in Control writes that kit into the folder.%n%nThe window is a bare frame. Hold the top edge to move it.
FinishedLabel=Ldash 1.0 is installed.%n%nThe first open creates your account and your folder. Close hides the window. Hold the top of the frame to move it. Ctrl+` shows it again. Quit from Control exits.%nYour notes stay in %%APPDATA%%\laden-ops and are kept if you uninstall.

[Code]
function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
  if (not Result) and RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
  if (not Result) and RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;
