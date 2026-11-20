Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "py -3 -X utf8 run_247.py", 0, False
MsgBox "Đã kích hoạt PVTK 24/7 Bot chạy ngầm hoàn toàn!" & vbCrLf & vbCrLf & "- Tool đang chạy âm thầm dưới nền Windows (không hiện cửa sổ)." & vbCrLf & "- Tự động quản lý nhận quà online, vấn đáp 22:01, săn đồ rẻ SGD, đoạt lì xì." & vbCrLf & "- Nhận thông báo & điều khiển mọi lúc qua Telegram." & vbCrLf & vbCrLf & "- Để dừng bot bất cứ lúc nào: Nhấp đúp vào file 'Dung_247.bat'.", 64, "PVTK 24/7 Automation"
