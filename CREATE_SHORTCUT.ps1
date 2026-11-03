$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $root 'GOTWiC Scanner.lnk'))
$shortcut.TargetPath = Join-Path $root 'RUN_GUI.bat'
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = (Join-Path $root 'GOTWiC_Scanner.ico') + ',0'
$shortcut.Description = 'GOTWiC Scanner'
$shortcut.Save()
