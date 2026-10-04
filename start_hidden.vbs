' Starts ScoutAI with no window and no taskbar entry. Logs go to scout.log.
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run """" & dir & "\venv\Scripts\pythonw.exe"" """ & dir & "\main.py""", 0, False
