#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-local"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "AlbionMerchantToolkit-Setup"
#endif

#define MyAppName "Albion Merchant Toolkit"
#define MyAppPublisher "Albion Merchant Toolkit"
#define MyAppExeName "AlbionMerchantToolkit.exe"

[Setup]
AppId={{7CB17B6A-4F1F-47CF-A86B-3AE0580D2282}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Albion Merchant Toolkit
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\installer-output
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} telepítő
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\AlbionMerchantToolkit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\AlbionPrizeShower.exe"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Asztali parancsikon létrehozása"; GroupDescription: "További lehetőségek:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} indítása"; Flags: nowait postinstall skipifsilent
