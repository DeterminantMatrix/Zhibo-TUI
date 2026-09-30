Option Explicit

Dim shell, fso, projectRoot, pythonw, command, smokeTest, exitCode, healthCommand
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectRoot = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pythonw = fso.BuildPath(projectRoot, ".venv\Scripts\pythonw.exe")

If Not fso.FileExists(pythonw) Then
    MsgBox "Python GUI runtime was not found: .venv\Scripts\pythonw.exe", 16, "Zhibo Quick"
    WScript.Quit 1
End If

healthCommand = Chr(34) & pythonw & Chr(34) & " -c " & Chr(34) & "import PySide6, yaml" & Chr(34)
exitCode = shell.Run(healthCommand, 0, True)
If exitCode <> 0 Then
    MsgBox "The .venv runtime is damaged or missing Qt dependencies. Recreate it and install requirements before starting Zhibo Quick.", 16, "Zhibo Quick"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectRoot
command = Chr(34) & pythonw & Chr(34) & " -m qt_quick"
smokeTest = WScript.Arguments.Named.Exists("smoke")
If smokeTest Then
    command = command & " --smoke-test"
End If

exitCode = shell.Run(command, 0, smokeTest)
If smokeTest Then
    WScript.Quit exitCode
End If
