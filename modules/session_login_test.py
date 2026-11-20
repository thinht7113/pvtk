import time
import os
from typing import List, Tuple
from core.network import PVTKConnection
from core.message import Message
import core.protocol as proto
from core.config import settings
from modules.free_proxy import FreeProxyManager

def test_login_from_sessions(filename: str = "sessionIDs.txt"):
    print("-> Chọn chế độ kiểm tra SessionID:")
    print("   [1] Đọc danh sách từ file (sessionIDs.txt)")
    print("   [2] Nhập thủ công SessionID")
    mode = input("-> Lựa chọn của bạn [1-2] (Enter=1): ").strip()

    sessions = []
    
    if mode == "2":
        sid_str = input("-> Nhập SessionID: ").strip()
        ukey_str = input("-> Nhập UKey (Mặc định 0): ").strip()
        
        session_id = int(sid_str) if sid_str.lstrip('-').isdigit() else 0
        ukey = int(ukey_str) if ukey_str.lstrip('-').isdigit() else 0
        sessions.append(("MANUAL_INPUT", session_id, ukey))
    else:
        if not os.path.exists(filename):
            print(f"\033[91m[LỖI]\033[0m Không tìm thấy file {filename}. Bạn cần chạy Option 12 trước để tạo file này.")
            return

        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    sessions.append((parts[0], int(parts[1]), int(parts[2])))
                elif len(parts) == 2:
                    sessions.append((parts[0], int(parts[1]), 0))

    if not sessions:
        print(f"\033[93m[CẢNH BÁO]\033[0m Không có SessionID nào để kiểm tra.")
        return

    print("\n" + "=" * 70)
    print(f"   ĐĂNG NHẬP GAME SERVER BẰNG SESSION_ID ({len(sessions)} TÀI KHOẢN)")
    print("=" * 70)

    for username, session_id, ukey in sessions:
        print(f"\n-> \033[96mĐang thử kết nối cho tài khoản: {username}\033[0m")
        print(f"   SessionID: {session_id} | UKey: {ukey}")
        
        # Connect to Game Server
        conn = PVTKConnection(settings.game_host, settings.game_port, timeout=5.0)
        if not conn.connect():
            print(f"   \033[91m[LỖI]\033[0m Không thể kết nối đến Game Server {settings.game_host}:{settings.game_port}")
            continue

        try:
            # Opcode 0x0001
            msg_handshake = Message(proto.CG_LOGIN_PLAYERCHECK)
            msg_handshake.putLong(ukey)
            msg_handshake.putInt(session_id)
            msg_handshake.putByte(5)
            msg_handshake.putInt(proto.CLIENT_BUILD_VERSION)
            msg_handshake.putByte(2)

            if not conn.send_message(msg_handshake):
                print(f"   \033[91m[LỖI]\033[0m Lỗi khi gửi gói tin Handshake")
                conn.close()
                continue
            
            print("   \033[93m[ĐÃ GỬI]\033[0m Đã gửi gói tin đăng nhập 0x0001. Đang chờ Server phản hồi...")
            
            # Listen to what server sends down for 5 seconds
            start_listen = time.time()
            has_response = False
            while time.time() - start_listen < 5.0:
                res = conn.receive_message(timeout=1.0)
                if res:
                    has_response = True
                    hex_data = res.data.hex().upper()
                    print(f"   📥 \033[92m[SERVER GỬI XUỐNG]\033[0m Opcode: 0x{res.type:04X} | Chiều dài: {len(res.data)} bytes")
                    print(f"      HEX: {hex_data}")
                    
                    if res.type == proto.CG_LOGIN_PLAYERCHECK:
                        code = res.getByte()
                        print(f"      -> Kết quả đăng nhập (Code): {code}")
                        if code == 0:
                            print("      -> \033[92mĐăng nhập thành công!\033[0m")
                        else:
                            print(f"      -> \033[91mĐăng nhập thất bại (SessionID có thể đã hết hạn)\033[0m")
                
            if not has_response:
                print("   \033[91m[TIMEOUT]\033[0m Không nhận được phản hồi nào từ Server sau 5 giây.")

        except Exception as e:
            print(f"   \033[91m[LỖI NGOẠI LỆ]\033[0m {e}")
        finally:
            conn.close()
            print("   \033[90m[ĐÓNG KẾT NỐI]\033[0m")
        
        time.sleep(1)
        
    print("\n-> Đã hoàn thành quá trình kiểm tra đăng nhập bằng SessionID.")
