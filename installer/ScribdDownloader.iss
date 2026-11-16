; Rust desktop installer with the matching Chrome for Testing runtime.
#define MyAppName "Scribd Downloader"
#define MyAppVersion "0.1.0"
#define MyAppExeName "scribd-rust-gui.exe"

[Setup]
AppId={{7C844B40-A7B0-4E48-9D8D-7D8813B3B50E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Scribd Downloader contributors
AppPublisherURL=https://github.com/Lordhussnain/scribd-downloader
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=ScribdDownloader-Rust-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\ScribdDownloader-Portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
