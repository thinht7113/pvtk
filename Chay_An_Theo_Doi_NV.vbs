Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "py modules\character_market_watcher.py", 0, False
MsgBox "Đã kích hoạt chế độ THEO DÕI SỞ GIAO DỊCH chạy ngầm hoàn toàn!" & vbCrLf & vbCrLf & "- Tool sẽ quét Nhân Vật (30s) và Vật Phẩm (15s) âm thầm dưới nền Windows." & vbCrLf & "- Bắn thông báo Telegram & Windows Toast khi có nhân vật mới hoặc đồ rẻ." & vbCrLf & vbCrLf & "⚠️ LƯU Ý: Chế độ này CHỈ quét Sở Giao Dịch!" & vbCrLf & "Để chạy ĐẦY ĐỦ hệ thống 24/7 (Vấn đáp 22:01, Rút thưởng, Treo nhận quà & Lệnh Telegram), hãy chạy file 'Chay_An_247.vbs' hoặc 'run_247.bat'!" & vbCrLf & vbCrLf & "- Để dừng: Nhấp đúp vào file 'Dung_Theo_Doi.bat'.", 64, "PVTK Market Watcher"
