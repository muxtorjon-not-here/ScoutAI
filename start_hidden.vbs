' Starts ScoutAI with no window and no taskbar entry. Logs go to scout.log.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
If Not fso.FileExists(dir & "\user_session.session") Or Not fso.FileExists(dir & "\.env") Then
    MsgBox "Run run.bat once first to finish the setup, then use this file.", 48, "ScoutAI"
    WScript.Quit
End If
sh.Run """" & dir & "\venv\Scripts\pythonw.exe"" """ & dir & "\main.py""", 0, False
