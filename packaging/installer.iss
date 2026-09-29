; CrystalX CoreView LCD for Windows
; Copyright (C) 2026 Abdul Moez (https://github.com/Abdul-Moez)
; SPDX-License-Identifier: GPL-3.0-or-later -- see the LICENSE file.
;
; Inno Setup script for CrystalX-LCD-Setup-<version>.exe. Built by
; .github/workflows/release.yml; by hand, after the PyInstaller build:
;
;   ISCC.exe /DAppVersion=1.0.0 /DPawnIOSetup=path\to\PawnIO_setup.exe packaging\installer.iss
;
; What it does:
;   install    copies the app to Program Files, installs PawnIO if it is
;              missing, installs and starts the CrystalXLCD service, adds the
;              Start menu entry and starts the tray app at every login
;   upgrade    the same, over the old version, keeping the user's settings
;   uninstall  removes all of that plus C:\ProgramData\CrystalX LCD (the
;              picture, settings and log). PawnIO stays: it is a shared driver
;              other programs may use, and it has its own uninstaller.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=1.0.0 ...
#endif
#ifndef DistDir
  #define DistDir "..\dist\CrystalX LCD"
#endif
#ifndef PawnIOSetup
  #error Pass the PawnIO installer: ISCC /DPawnIOSetup=path\to\PawnIO_setup.exe ...
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName "CrystalX LCD"
#define Repo "https://github.com/Abdul-Moez/CrystalX-CoreView-Software-For-Windows"

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of this app.
AppId={{FDFC7720-0C4F-4561-AAD7-08B13EA88C58}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Abdul Moez
AppPublisherURL=https://github.com/Abdul-Moez
AppSupportURL={#Repo}/issues
AppUpdatesURL={#Repo}/releases
; Always Program Files: the service runs as SYSTEM, so its files must be in a
; folder that normal users cannot modify. Hence no folder choice.
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
SetupIconFile=..\assets\crystalx-lcd.ico
UninstallDisplayIcon={app}\CrystalXLCD.exe
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=CrystalX-LCD-Setup-{#AppVersion}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
SetupLogging=yes
; The app and service are closed by PrepareToInstall below.
CloseApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Abdul Moez
VersionInfoDescription={#AppName} setup
VersionInfoCopyright=Copyright (C) 2026 Abdul Moez. GPL-3.0-or-later.
VersionInfoProductName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "disablelcdcontrol"; \
  Description: "Stop the vendor's LCD Control from starting with Windows (only one program can use the screen; you can still open it yourself)"; \
  Check: LcdControlStartsWithWindows

[InstallDelete]
; An upgrade replaces the bundled runtime completely rather than leaving old
; files next to new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PawnIOSetup}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedPawnIO

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\CrystalXLCD.exe"; \
  Comment: "Clock, date and PC stats on the CrystalX CoreView case screen"

[Registry]
; The tray app starts quietly (no window) at every user's login.
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\CrystalXLCD.exe"" --hidden"; Flags: uninsdeletevalue

[Run]
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; \
  StatusMsg: "Installing the PawnIO driver (needed for the CPU temperature)..."; \
  Flags: waituntilterminated; Check: NeedPawnIO
Filename: "{app}\CrystalXLCD-Service.exe"; Parameters: "--startup auto install"; \
  StatusMsg: "Setting up the CrystalX LCD service..."; \
  Flags: runhidden waituntilterminated; Check: not ServiceExists
Filename: "{sys}\schtasks.exe"; Parameters: "/change /tn ""LCD ControlPowerBoot"" /disable"; \
  Flags: runhidden waituntilterminated; Tasks: disablelcdcontrol
Filename: "{sys}\sc.exe"; Parameters: "start CrystalXLCD"; \
  StatusMsg: "Starting the display..."; Flags: runhidden waituntilterminated
Filename: "{app}\CrystalXLCD.exe"; Description: "Open {#AppName}"; \
  Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\sc.exe"; Parameters: "delete CrystalXLCD"; \
  Flags: runhidden waituntilterminated; RunOnceId: "DeleteService"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\{#AppName}"

[Code]
function ServiceExists: Boolean;
begin
  Result := RegKeyExists(HKLM64, 'SYSTEM\CurrentControlSet\Services\CrystalXLCD');
end;

{ PawnIO 2.0 or newer is kept; anything older, or none, gets 2.2.0. }
function NeedPawnIO: Boolean;
var
  Version: String;
  Dot: Integer;
begin
  Result := True;
  if RegQueryStringValue(HKLM64,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO',
      'DisplayVersion', Version) then
  begin
    Dot := Pos('.', Version);
    if Dot > 0 then
      Result := StrToIntDef(Copy(Version, 1, Dot - 1), 0) < 2;
  end;
end;

function LcdControlStartsWithWindows: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\schtasks.exe'),
                 '/query /tn "LCD ControlPowerBoot"', '', SW_HIDE,
                 ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure Run(const Exe, Params: String);
var
  Code: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ Close the tray app and stop the service (waiting until it has stopped), so
  their files can be replaced or removed. }
procedure StopApp;
begin
  Run(ExpandConstant('{sys}\taskkill.exe'), '/f /im CrystalXLCD.exe');
  if ServiceExists then
    Run(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
        '-NoProfile -Command "Stop-Service -Name CrystalXLCD -Force -ErrorAction SilentlyContinue"');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopApp;
  { The script version's autostart task would hold the screen too. }
  Run(ExpandConstant('{sys}\schtasks.exe'), '/end /tn "CrystalX LCD"');
  Run(ExpandConstant('{sys}\schtasks.exe'), '/delete /tn "CrystalX LCD" /f');
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopApp;
end;
