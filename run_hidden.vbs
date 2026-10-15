' Hidden launcher: run a .bat with NO console window (nothing to close, nothing to bother)
' Usage: wscript.exe run_hidden.vbs <full-path-to-bat>
CreateObject("WScript.Shell").Run """" & WScript.Arguments(0) & """", 0, False
