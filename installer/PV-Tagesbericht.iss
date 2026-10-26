; Inno Setup 6.3 oder neuer. Bauen: .\build_installer.ps1  (oder: ISCC.exe /DAppVersion=2.1.0 installer\PV-Tagesbericht.iss)
; Installiert pro Benutzer (ohne Administratorrechte) nach %LOCALAPPDATA%\Programs\PV-Tagesbericht.
; Daten und Einstellungen liegen getrennt unter %LOCALAPPDATA%\PV-Tagesbericht und bleiben bei Updates erhalten.

#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif
#define AppName "PV-Tagesbericht"
#define ServerExe "PV-Tagesbericht-Server.exe"

[Setup]
AppId={{F28C8F8A-FC79-41EA-9E7F-E87612DF3B62}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=PV-Tagesbericht-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#ServerExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "autostart"; Description: "Beim Anmelden automatisch starten (empfohlen: so werden die Daten täglich aktualisiert)"; GroupDescription: "Zusätzliche Aufgaben:"
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop anlegen"; GroupDescription: "Zusätzliche Aufgaben:"; Flags: unchecked

[InstallDelete]
; Bei einem Update alte Bibliotheken entfernen, damit keine veralteten Dateien zurückbleiben.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PV-Tagesbericht\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Parameters: "serve"; WorkingDir: "{app}"; Comment: "Bericht öffnen und Einstellungen ändern"
Name: "{autoprograms}\{#AppName} beenden"; Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#ServerExe}"; Flags: runminimized; Comment: "Beendet den PV-Tagesbericht-Server (die tägliche Aktualisierung pausiert dann)"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Parameters: "serve"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autostartup}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Parameters: "serve --no-browser"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#ServerExe}"; Parameters: "serve"; WorkingDir: "{app}"; Description: "PV-Tagesbericht jetzt starten und einrichten"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopServer();
var
  ResultCode: Integer;
begin
  // Läuft der Server noch (auch als Hintergrund-Aktualisierung), blockiert er das Ersetzen der Dateien.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#ServerExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopServer();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
    StopServer();
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PV-Tagesbericht');
    if (not UninstallSilent()) and DirExists(DataDir) then
      if MsgBox('Sollen auch die gespeicherte Tageshistorie und die Einstellungen (inklusive AlphaESS-Zugangsdaten) gelöscht werden?' + #13#10 + #13#10 + DataDir + #13#10 + #13#10 + 'Bei "Nein" bleiben sie für eine spätere Neuinstallation erhalten.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
