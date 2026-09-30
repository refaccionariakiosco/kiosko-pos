; Instalador de Kiosco POS (build onedir) para cualquier máquina Windows.
; Uso: & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" KioscoPOS.iss

#define MyAppName "Kiosco POS"
#ifndef MyAppVersion
#define MyAppVersion "1.2.0"
#endif
#define MyAppExeName "KioscoPOS.exe"

[Setup]
AppId={{B2D8E0A1-3C4D-4E5F-9A6B-7C8D9E0F1A2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Kiosco POS
DefaultDirName={localappdata}\KioscoPOS
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\icono.ico
UninstallDisplayIcon={app}\icono.ico
OutputDir=..\dist
OutputBaseFilename=SetupKioscoPOS-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
MinVersion=10.0.17763
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el Escritorio"; GroupDescription: "Accesos directos:"; Flags: checkedonce

[Files]
Source: "..\dist\KioscoPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data"
Source: "..\icono.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icono.ico"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icono.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"