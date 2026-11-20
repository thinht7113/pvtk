"""
PVTK Headless Protocol Tool - Main Entry Point
CLI Dashboard for automated tasks.
Execution Flow:
1. BƯỚC 1: Chọn Máy Chủ (Server Selection)
2. BƯỚC 2: Nạp File Tài Khoản Riêng Biệt & Thiết Lập Phạm Vi (Account Range Selection)
3. BƯỚC 3: Bảng Điều Khiển 11 Tính Năng (Main Action Menu)
"""

import sys
import os
import time
import json
import signal

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        
        # Set UTF-8 code page (65001) for console input and output
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)

        # Prevent Windows Power Throttling / sleep when minimized
        try:
            kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass

        # Disable QuickEdit mode and Mouse Input on STD_INPUT_HANDLE (-10)
        # to prevent CMD from freezing when clicked or minimized
        try:
            h_in = kernel32.GetStdHandle(-10)
            mode_in = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_in, ctypes.byref(mode_in)):
                new_mode_in = (mode_in.value & ~0x0040 & ~0x0010) | 0x0080
                kernel32.SetConsoleMode(h_in, new_mode_in)
        except Exception:
            pass

        for h_id in (-11, -12):
            h = kernel32.GetStdHandle(h_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(h, mode.value | 0x0004 | 0x0008)

        # Automatically switch legacy Raster Fonts to TrueType (Consolas / Cascadia Mono)
        # to prevent Vietnamese accents from becoming '?'
        h_con = kernel32.CreateFileW("CONOUT$", 0x40000000 | 0x80000000, 1 | 2, None, 3, 0, None)
        if h_con != -1:
            class COORD(ctypes.Structure):
                _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
            class CONSOLE_FONT_INFOEX(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.ULONG),
                    ("nFont", wintypes.DWORD),
                    ("dwFontSize", COORD),
                    ("FontFamily", wintypes.UINT),
                    ("FontWeight", wintypes.UINT),
                    ("FaceName", ctypes.c_wchar * 32)
                ]
            for font_name in ["Cascadia Mono", "Consolas", "Lucida Console"]:
                new_font = CONSOLE_FONT_INFOEX()
                new_font.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
                new_font.nFont = 0
                new_font.dwFontSize = COORD(0, 16)
                new_font.FontFamily = 54
                new_font.FontWeight = 400
                new_font.FaceName = font_name
                if kernel32.SetCurrentConsoleFontEx(h_con, False, ctypes.byref(new_font)):
                    break
            kernel32.CloseHandle(h_con)
    except Exception:
        pass
# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import settings, slugify_server_name
from modules.auth import AuthManager, CharacterInfo
from modules.free_proxy import FreeProxyManager
from modules.runner import run_all_accounts
from modules.lottery_runner import run_all_lottery_accounts
from modules.teleport_all import run_teleport_menu
from modules.mail_sender import run_all_mail_sender
from modules.mail_claim import run_all_mail_claim
from modules.exp_ticket_sender import run_all_exp_ticket_sender
from modules.server_manager import run_server_manager_cli, count_accounts_in_file
from core.tele_notifier import TeleNotifier, install_global_error_handlers

# Kích hoạt bắt lỗi toàn cục lên Telegram
install_global_error_handlers()

import threading
PRINT_LOCK = threading.Lock()

def safe_print(msg):
    with PRINT_LOCK:
        try:
            print(msg, flush=True)
        except Exception:
            try:
                print(str(msg).encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
            except Exception:
                pass

from modules.character_creator import run_mass_create_characters
from modules.bag_cleaner import run_all_bag_cleaner
from modules.action_mirror import run_action_mirror_cli
from modules.online_reward_runner import run_all_online_reward
from modules.country_manager import run_country_manager_cli

def load_accounts(filepath: str):
    """
    Loads accounts from file.
    Supports formats:
    - username|password
    - username|password|proxy (e.g. 103.14.2.1:8080 or socks5://user:pass@ip:port)
    """
    accounts = []
    if not os.path.exists(filepath):
        return accounts

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                user, pwd, proxy = parts[0], parts[1], parts[2]
                accounts.append((user, pwd, proxy if proxy else None))
            elif len(parts) >= 2:
                user, pwd = parts[0], parts[1]
                accounts.append((user, pwd, None))
    return accounts


def prompt_startup_server_selection():
    """
    BƯỚC 1: Hiển thị bảng chọn máy chủ ngay khi bật tool.
    Cho phép chọn máy chủ trước khi đọc file tài khoản.
    """
    settings.reload()
    servers = settings.servers
    curr_idx = settings.selected_server_index

    print("\n" + "=" * 85)
    print("                     BƯỚC 1: CHỌN MÁY CHỦ PVTK (SERVER SELECTOR)")
    print("=" * 85)
    print(f"{'STT':<4} | {'TÊN MÁY CHỦ':<24} | {'GAME SERVER':<20} | {'FILE ACC (SỐ LƯỢNG)':<26}")
    print("-" * 85)

    for i, srv in enumerate(servers):
        name = srv.get("name", f"Server {i+1}")
        g_host = srv.get("game_host", "127.0.0.1")
        g_port = srv.get("game_port", 10000)
        conn_str = f"{g_host}:{g_port}"
        
        acc_file = srv.get("accounts_file") or slugify_server_name(name)
        acc_cnt = count_accounts_in_file(acc_file)
        acc_display = f"{acc_file} ({acc_cnt} acc)"

        is_active = (i == curr_idx)
        star = "★" if is_active else " "
        tag = " \033[92m● ĐANG CHỌN\033[0m" if is_active else ""

        print(f"  [{i+1}] {star} {name:<26} | {conn_str:<20} | {acc_display:<35}{tag}")

    print("-" * 85)
    print(f"  [1 - {len(servers)}] Nhập số thứ tự để CHỌN máy chủ")
    print(f"  [Enter]  Dùng máy chủ mặc định: \033[92m[{curr_idx+1}] {servers[curr_idx].get('name')}\033[0m")
    print("=" * 85)

    try:
        choice = input(f"-> Chọn máy chủ [1-{len(servers)}] (Enter={curr_idx+1}): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                settings.select_server(idx)
                print(f"-> \033[92m[ĐÃ CHỌN]\033[0m {servers[idx].get('name')} ({servers[idx].get('game_host')}:{servers[idx].get('game_port')})")
    except (KeyboardInterrupt, EOFError):
        pass

    settings.reload()


def ask_account_range(total_available: int, server_name: str, accounts_file: str):
    """
    BƯỚC 2: Thiết lập phạm vi tài khoản của máy chủ vừa chọn.
    """
    if total_available == 0:
        return 0, 0

    while True:
        try:
            print("\n" + "=" * 75)
            print("                 BƯỚC 2: THIẾT LẬP PHẠM VI TÀI KHOẢN")
            print("=" * 75)
            print(f"-> Máy chủ: \033[93m{server_name}\033[0m")
            print(f"-> File tài khoản: \033[96m{accounts_file}\033[0m")
            print(f"-> Tổng cộng tìm thấy: \033[92m{total_available}\033[0m tài khoản (từ trên xuống dưới)")
            print("-> Cách nhập: ")
            print(f"   * Nhập 1 số (Ví dụ: 30)      -> Chạy từ acc 1 đến acc 30")
            print(f"   * Nhập khoảng (Ví dụ: 10-35)  -> Chạy từ acc 10 đến acc 35")
            print(f"   * Nhấn Enter                -> Chạy tất cả {total_available} acc")
            inp = input(f"-> Vui lòng nhập phạm vi [1-{total_available}] (Enter={total_available}): ").strip()

            if not inp:
                return 1, total_available

            # Check if input is a range (e.g., '10-35', '10:35', '10 35')
            for sep in ["-", ":", " ", ","]:
                if sep in inp:
                    parts = [p.strip() for p in inp.split(sep) if p.strip()]
                    if len(parts) == 2:
                        start = int(parts[0])
                        end = int(parts[1])
                        if start > end:
                            start, end = end, start
                        start = max(1, min(start, total_available))
                        end = max(1, min(end, total_available))
                        return start, end

            # Single number
            val = int(inp)
            if 1 <= val <= total_available:
                return 1, val
            elif val > total_available:
                print(f"\033[93m[CẢNH BÁO]\033[0m Bạn nhập {val} lớn hơn tổng số ({total_available}). Sẽ chọn tất cả.")
                return 1, total_available
            else:
                print("\033[91m[LỖI]\033[0m Số lượng tài khoản phải lớn hơn 0.")
        except ValueError:
            print("\033[91m[LỖI]\033[0m Vui lòng nhập định dạng hợp lệ (Ví dụ: 30 hoặc 10-35).")
        except (KeyboardInterrupt, EOFError):
            return 1, total_available


def main():
    # BƯỚC 1: Chọn Máy Chủ Trước
    prompt_startup_server_selection()

    # BƯỚC 2: Nạp File tài khoản của Server đó và Thiết Lập Phạm Vi
    settings.reload()
    all_accounts = load_accounts(settings.accounts_file)
    total_available = len(all_accounts)

    if total_available > 0:
        start_idx, end_idx = ask_account_range(total_available, settings.game_server_name, settings.accounts_file_basename)
    else:
        start_idx, end_idx = 0, 0

    # BƯỚC 3: Menu Tính Năng Chính
    while True:
        settings.reload()
        all_accounts = load_accounts(settings.accounts_file)
        FreeProxyManager.set_accounts_file(settings.accounts_file)
        total_available = len(all_accounts)
        
        # Clamp range
        if total_available > 0:
            start_idx = max(1, min(start_idx, total_available))
            end_idx = max(start_idx, min(end_idx, total_available))
            active_accounts = all_accounts[start_idx - 1 : end_idx]
        else:
            start_idx, end_idx = 0, 0
            active_accounts = []

        print("\n" + "=" * 75)
        print("                HỆ THỐNG PVTK HEADLESS PROTOCOL TOOL")
        print("===========================================================================")
        print(f"-> Máy chủ: \033[93m{settings.game_server_name}\033[0m ({settings.game_host}:{settings.game_port}) | Luồng: {settings.max_concurrent_accounts}")
        if total_available > 0:
            print(f"-> File Acc: \033[96m{settings.accounts_file_basename}\033[0m | Phạm vi: \033[92m{len(active_accounts)} / {total_available} acc\033[0m ({start_idx} -> {end_idx})")
        else:
            print(f"-> File Acc: \033[96m{settings.accounts_file_basename}\033[0m | \033[91m(File đang trống - Hãy dán tài khoản vào file này)\033[0m")
        print("---------------------------------------------------------------------------")
        print("  [1] Chạy sự kiện Vấn Đáp (Tại chỗ / Đa luồng)")
        print("  [2] Chạy sự kiện Rút Thưởng May Mắn (Quảng Trường Tân Nguyệt / Bạc)")
        print("  [3] \033[96m🚀 Dịch chuyển bản đồ / Thoát lag (Thánh Vực, Tân Nguyệt Trấn, Thoát lag)\033[0m")
        print("  [4] Gửi thư giao dịch COD (Tự động chuyển Bạc đến nhân vật nhận)")
        print("  [5] Nhận Thư (Thư thưởng nhiệm vụ & Xác nhận/Trả về)")
        print("  [6] Gửi toàn bộ Vé EXP Nhân Vật (Không khóa) cho ID người nhận")
        print("  [7] \033[93m💸 Gửi toàn bộ Bạc đang có cho ID người nhận\033[0m")
        print("  [8] \033[93m⚙️  Chọn / Đổi Máy Chủ (Server Selector & Ping Test)\033[0m")
        print("  [9] \033[92m🧙‍♂️ Tự động tạo đủ 3 nhân vật cho toàn bộ acc (Phái Hiệp Khách, Nam/Nữ Random)\033[0m")
        print("  [10] \033[96m🚀 Ghi Kịch Bản Thông Minh\033[0m")
        print("  [11] \033[95m🤖 Chạy Kịch Bản Qua Giả Lập LDPlayer (ADB Automation)\033[0m")
        print("  [12] \033[93m🎁 Treo Game Online & Tự Động Nhận Quà Online (AFK Keep-Alive)\033[0m")
        print("  [13] \033[93m🎁 Treo Game Online Cùng Lúc 2 Server (Tuyệt Vô Thần & Phụng Thiên)\033[0m")
        print("  [14] \033[96m⚡ Dùng Vé EXP Nhân Vật (Khóa)\033[0m")
        print("  [15] \033[92m📦 Gửi Vật Phẩm Bất Kỳ (VD: Rương cấp 50) cho ID người nhận\033[0m")
        print("  [16] \033[96m🔍 Đăng nhập Game Server bằng SessionID (Từ Option 12) và xem log Server\033[0m")
        print("  [17] \033[95m🏰 Quản Lý Quốc Gia (Cống Hiến, Xin Vào, Rời Bang Hàng Loạt)\033[0m")
        print("  [18] \033[91m🗑️  Xóa Vật Phẩm Chỉ Định Trong Túi Đồ (Nhiều từ khóa cách nhau bởi ',')\033[0m")
        print("  [19] \033[93m🔔 Theo Dõi Sở Giao Dịch & Tự Đoạt Lì Xì Thế Giới (Săn Đồ Rẻ)\033[0m")
        print("  [C] Thay đổi số lượng tài khoản đang chọn")
        print("  [0] Thoát chương trình")
        print("======================================================================")

        try:
            choice = input("-> Vui lòng chọn chức năng [0-19, C]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nTạm biệt!")
            break

        if choice == "0":
            print("\nCảm ơn bạn đã sử dụng PVTK Tool. Tạm biệt!\n")
            break

        elif choice.upper() == "C":
            if total_available > 0:
                start_idx, end_idx = ask_account_range(total_available, settings.game_server_name, settings.accounts_file_basename)
            else:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' đang trống. Hãy dán tài khoản vào file trước!")
            continue

        elif choice == "1":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_accounts(proxy_accounts, max_workers=settings.max_concurrent_accounts)
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "2":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_lottery_accounts(proxy_accounts, max_workers=settings.max_concurrent_accounts)
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "3":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_teleport_menu(proxy_accounts, max_workers=settings.max_concurrent_accounts)

        elif choice == "4":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            
            print("\n" + "=" * 70)
            print("                 GỬI THƯ GIAO DỊCH BẠC (COD MAIL)")
            print("=" * 70)
            receiver_default = settings.mail_receiver_target or "Chưa cấu hình"
            target_str = input(f"-> Nhập ID / UID hoặc Tên nhân vật nhận thư [{receiver_default}]: ").strip()
            if not target_str:
                target_str = settings.mail_receiver_target

            if not target_str:
                print("\033[93m[ĐÃ HỦY]\033[0m Người nhận không được để trống.")
                continue

            silver_req_str = input(f"-> Số Bạc yêu cầu người nhận thanh toán [Mặc định: {settings.mail_receive_silver:,}]: ").strip()
            silver_req = int(silver_req_str) if silver_req_str.isdigit() else settings.mail_receive_silver

            silver_send_str = input(f"-> Số Bạc đính kèm gửi tặng [Mặc định: {settings.mail_send_silver}]: ").strip()
            silver_send = int(silver_send_str) if silver_send_str.isdigit() else settings.mail_send_silver

            print("-" * 70)
            print(f"-> Người nhận: \033[92m'{target_str}'\033[0m")
            print(f"-> Yêu cầu thanh toán (Nhận): \033[93m{silver_req:,} Bạc\033[0m | Đính kèm gửi (Tặng): \033[96m{silver_send} Bạc\033[0m")
            print("-> Bắt đầu tiến trình gửi thư đa luồng...")

            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_mail_sender(
                proxy_accounts,
                target_str=target_str,
                receive_silver=silver_req,
                send_silver=silver_send,
                content=settings.mail_content,
                max_workers=settings.max_concurrent_accounts
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "5":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_mail_claim(proxy_accounts, max_workers=settings.max_concurrent_accounts)
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "6":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            
            print("\n" + "=" * 70)
            print("     GỬI TOÀN BỘ VÉ EXP NHÂN VẬT (KHÔNG KHÓA) ĐẾN ID NGƯỜI NHẬN")
            print("=" * 70)
            target_str = input("-> Nhập ID / UID hoặc Tên nhân vật nhận thư: ").strip()
            if not target_str:
                print("\033[93m[ĐÃ HỦY]\033[0m Người nhận không được để trống.")
                continue

            print("-" * 70)
            print(f"-> Người nhận: \033[92m'{target_str}'\033[0m")
            print("-> \033[93m[LƯU Ý]\033[0m Hệ thống CHỈ gửi Vé EXP Nhân Vật KHÔNG KHÓA. Vé tự động khóa sẽ được giữ nguyên.")
            
            print("\n-> \033[96m[BƯỚC KIỂM TRA MẪU]\033[0m Đang tìm 1 nhân vật có vé EXP để gửi thư test...")
            from modules.exp_ticket_sender import run_test_item_mail
            test_success = run_test_item_mail(active_accounts, target_str)
            
            if not test_success:
                print("\033[91m[LỖI]\033[0m Quét qua các tài khoản nhưng không tìm thấy vé EXP không khóa nào hoặc lỗi mạng. Hủy gửi hàng loạt.")
                try:
                    input("\nNhấn Enter để quay lại Menu...")
                except (KeyboardInterrupt, EOFError):
                    pass
                continue
                
            confirm = input(f"\n-> Bạn có nhận được thư test không? Gõ 'y' để gửi hàng loạt, phím khác để hủy: ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("\033[93m[ĐÃ HỦY]\033[0m Đã hủy quá trình gửi vé EXP hàng loạt.")
                try:
                    input("\nNhấn Enter để quay lại Menu...")
                except (KeyboardInterrupt, EOFError):
                    pass
                continue

            limit_str = ""
            try:
                limit_str = input("-> Nhập số lượng thư tối đa muốn gửi (Nhấn Enter để gửi TẤT CẢ): ").strip()
            except (KeyboardInterrupt, EOFError):
                pass
            max_mails = int(limit_str) if limit_str.isdigit() and int(limit_str) > 0 else None

            print("\n-> Bắt đầu tiến trình gửi vé EXP hàng loạt...")
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_exp_ticket_sender(
                proxy_accounts,
                target=target_str,
                max_workers=settings.max_concurrent_accounts,
                max_mails=max_mails
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "8":
            run_server_manager_cli()
            settings.reload()
            new_accs = load_accounts(settings.accounts_file)
            if len(new_accs) > 0:
                start_idx, end_idx = ask_account_range(len(new_accs), settings.game_server_name, settings.accounts_file_basename)
            else:
                start_idx, end_idx = 0, 0
            continue

        elif choice == "9":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_mass_create_characters(proxy_accounts, target_slots=3, max_workers=settings.max_concurrent_accounts)
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "10":
            run_action_mirror_cli()
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
                
        elif choice == "15":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            
            print("\n" + "=" * 70)
            print("     GỬI TOÀN BỘ VẬT PHẨM (THEO TỪ KHÓA) ĐẾN ID NGƯỜI NHẬN")
            print("=" * 70)
            target_str = input("-> Nhập ID / UID hoặc Tên nhân vật nhận thư: ").strip()
            if not target_str:
                print("\033[93m[ĐÃ HỦY]\033[0m Người nhận không được để trống.")
                continue

            item_keyword_input = input("-> Nhập từ khóa tên vật phẩm cần gửi (VD: rương cấp 50, rương vũ khí): ").strip()
            if not item_keyword_input:
                print("\033[93m[ĐÃ HỦY]\033[0m Từ khóa vật phẩm không được để trống.")
                continue

            keywords = [k.strip().lower() for k in item_keyword_input.split(",") if k.strip()]

            print("-" * 70)
            print(f"-> Người nhận: \033[92m'{target_str}'\033[0m")
            print(f"-> Từ khóa vật phẩm: \033[93m{keywords}\033[0m")
            print("-> \033[93m[LƯU Ý]\033[0m Hệ thống CHỈ gửi vật phẩm KHÔNG KHÓA.")
            
            print("\n-> \033[96m[BƯỚC KIỂM TRA MẪU]\033[0m Đang tìm 1 nhân vật có vật phẩm phù hợp để gửi thư test...")
            from modules.exp_ticket_sender import run_test_item_mail
            test_success = run_test_item_mail(active_accounts, target_str, keywords=keywords, item_name_display=item_keyword_input.upper())
            
            if not test_success:
                print("\033[91m[LỖI]\033[0m Quét qua các tài khoản nhưng không tìm thấy vật phẩm nào khớp từ khóa (không khóa) hoặc lỗi mạng. Hủy gửi hàng loạt.")
                try:
                    input("\nNhấn Enter để quay lại Menu...")
                except (KeyboardInterrupt, EOFError):
                    pass
                continue
            
            confirm = input(f"\n-> Bạn có nhận được thư test không? Gõ 'y' để gửi hàng loạt, phím khác để hủy: ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("\033[93m[ĐÃ HỦY]\033[0m Đã hủy quá trình.")
                continue

            limit_str = ""
            try:
                limit_str = input("-> Nhập số lượng thư tối đa muốn gửi (Nhấn Enter để gửi TẤT CẢ): ").strip()
            except (KeyboardInterrupt, EOFError):
                pass
            max_mails = int(limit_str) if limit_str.isdigit() and int(limit_str) > 0 else None

            print("\n-> Bắt đầu tiến trình gửi vật phẩm hàng loạt...")
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_exp_ticket_sender(
                proxy_accounts,
                target=target_str,
                max_workers=settings.max_concurrent_accounts,
                keywords=keywords,
                mail_title=f"Gửi {item_keyword_input}",
                log_title=item_keyword_input.upper(),
                max_mails=max_mails
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
                
        elif choice == "16":
            from modules.session_login_test import test_login_from_sessions
            test_login_from_sessions("sessionIDs.txt")
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "17":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
            else:
                run_country_manager_cli(active_accounts)
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "18":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue

            print("\n" + "=" * 75)
            print("     🗑️  XÓA VẬT PHẨM CHỈ ĐỊNH TRONG TÚI ĐỒ (HÀNG LOẠT NHÂN VẬT)")
            print("=" * 75)
            print("-> Ví dụ: Nhập 'thuốc' để xóa mọi vật phẩm chứa chữ 'thuốc' (Thuốc trị thương, Hoạt lực thuốc...)")
            print("-> Nếu muốn xóa nhiều loại vật phẩm cùng lúc, hãy ngăn cách bằng dấu phẩy ',' (VD: thuốc, phù, dược thảo)")
            print("-" * 75)
            
            raw_keywords = input("-> Nhập từ khóa vật phẩm cần xóa: ").strip()
            if not raw_keywords:
                print("\033[93m[ĐÃ HỦY]\033[0m Từ khóa không được để trống.")
                continue

            keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
            if not keywords:
                print("\033[93m[ĐÃ HỦY]\033[0m Danh sách từ khóa không hợp lệ.")
                continue

            print(f"\n-> Danh sách từ khóa sẽ quét và xóa: \033[93m{keywords}\033[0m")
            print("-> \033[91m[CẢNH BÁO]\033[0m Toàn bộ vật phẩm trong túi đồ có tên chứa các từ khóa trên sẽ bị XÓA BỎ!")
            print("-> (Trang bị đang mặc trên người slot 0-29 được bảo vệ tuyệt đối, KHÔNG BỊ ẢNH HƯỞNG).")
            confirm = input("-> Bạn có chắc chắn muốn xóa? Gõ 'y' để tiếp tục, phím khác để hủy: ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("\033[93m[ĐÃ HỦY]\033[0m Đã hủy quá trình xóa vật phẩm.")
                continue

            default_workers = min(len(active_accounts), settings.max_concurrent_accounts)
            print(f"\n-> Số tài khoản chạy đồng thời (Số luồng) [1-{len(active_accounts)}] (Mặc định: {default_workers}): ", end="")
            workers_inp = input().strip()
            workers = int(workers_inp) if (workers_inp.isdigit() and int(workers_inp) > 0) else default_workers

            from modules.item_deleter import run_all_item_deleter
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_item_deleter(
                proxy_accounts,
                keywords=keywords,
                max_workers=workers
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "19":
            FreeProxyManager.stop_background_worker()
            from modules.character_market_watcher import run_watcher
            run_watcher()
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "11":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue
            
            print("\n" + "=" * 75)
            print("        CHẠY KỊCH BẢN QUA GIẢ LẬP LDPLAYER (ADB AUTOMATION)")
            print("=" * 75)
            print("1. Cấu hình tọa độ click (Chỉ cần làm 1 lần)")
            print("2. Chạy Kịch Bản Tự Động (Chạy 3 Giả Lập)")
            sub_choice = input("-> Chọn [1/2]: ").strip()
            
            if sub_choice == "1":
                from modules.adb_setup import calibrate_interactive
                from modules.adb_controller import ADBController
                import threading
                import socket
                import select
                import subprocess
                
                # Mở 3 cổng Smart Proxy 10001, 10002, 10003: tự động điều hướng Master Login (30000) và Game World (settings.game_port)
                print(f"🔌 Đang mở cổng Smart Proxy [10001, 10002, 10003] (Master {settings.master_port} + Game {settings.game_port}) để vào game lấy tọa độ...")
                
                def start_smart_proxy(port):
                    try:
                        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        server.bind(("0.0.0.0", port))
                        server.listen(5)
                        
                        def handle_client(c_sock):
                            r_sock = None
                            try:
                                c_sock.settimeout(10.0)
                                initial_data = c_sock.recv(4096)
                                if not initial_data:
                                    c_sock.close()
                                    return
                                
                                msg_type = 0
                                if len(initial_data) >= 4:
                                    msg_type = (initial_data[2] << 8) | initial_data[3]
                                
                                is_master = ((msg_type >> 8) == 0x13) or (0x1300 <= msg_type <= 0x13FF)
                                dest_host = settings.master_host if is_master else settings.game_host
                                dest_port = settings.master_port if is_master else settings.game_port
                                
                                r_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                r_sock.connect((dest_host, dest_port))
                                r_sock.sendall(initial_data)
                                c_sock.settimeout(None)
                                
                                socks = [c_sock, r_sock]
                                while True:
                                    r, _, x = select.select(socks, [], socks, 1.0)
                                    if x:
                                        break
                                    for s in r:
                                        data = s.recv(4096)
                                        if not data:
                                            return
                                        if s is c_sock:
                                            r_sock.sendall(data)
                                        else:
                                            c_sock.sendall(data)
                            except Exception:
                                pass
                            finally:
                                try:
                                    c_sock.close()
                                except:
                                    pass
                                try:
                                    if r_sock:
                                        r_sock.close()
                                except:
                                    pass
                                    
                        while True:
                            try:
                                client_sock, _ = server.accept()
                                threading.Thread(target=handle_client, args=(client_sock,), daemon=True).start()
                            except Exception:
                                pass
                    except Exception:
                        pass
                
                for p in [10001, 10002, 10003]:
                    threading.Thread(target=start_smart_proxy, args=(p,), daemon=True).start()
                

                
                # Auto detect all running devices
                out = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', 'devices'], capture_output=True, text=True).stdout
                devices = []
                for line in out.splitlines()[1:]:
                    if 'device' in line and 'offline' not in line:
                        devices.append(line.split()[0])
                        
                if not devices:
                    print("\033[91m[LỖI]\033[0m Không tìm thấy giả lập nào đang chạy! Vui lòng mở LDPlayer.")
                else:
                    # Map reverse port cho TỪNG GIẢ LẬP (1 port/emulator - APK chỉ dùng 10002)
                    for i, dev in enumerate(devices):
                        game_port = 10001 + i * 2      # 10001, 10003, 10005
                        
                        safe_print(f"  🔧 [SETUP] Configuring {dev} -> port {game_port}...")
                        
                        # ADB Reverse: emulator localhost:10002 -> host:game_port
                        result = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', dev, 'reverse', f'tcp:10002', f'tcp:{game_port}'], capture_output=True, text=True)
                        if result.returncode != 0:
                            safe_print(f"  ❌ [SETUP] ADB reverse failed: {result.stderr}")
                        else:
                            safe_print(f"  ✅ [SETUP] ADB reverse OK: tcp:10002 -> tcp:{game_port}")
                        
                        # Verify reverse
                        verify = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', dev, 'reverse', '--list'], capture_output=True, text=True)
                        safe_print(f"  🔍 [SETUP] ADB reverse --list:\n{verify.stdout}")
                        
                        # iptables REDIRECT (10.0.2.2:10002 -> localhost:10002)
                        iptables_cmd = 'su 0 sysctl -w net.ipv4.conf.all.route_localnet=1; su 0 iptables -t nat -F OUTPUT; su 0 iptables -t nat -A OUTPUT -p tcp -d 10.0.2.2 --dport 10002 -j REDIRECT'
                        result = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', dev, 'shell', iptables_cmd], capture_output=True, text=True)
                        if result.returncode != 0:
                            safe_print(f"  ❌ [SETUP] iptables failed: {result.stderr}")
                        else:
                            safe_print(f"  ✅ [SETUP] iptables REDIRECT OK")
                        
                        safe_print(f"  🔌 Emulator {i+1} [{dev}]: 10.0.2.2:10002 -> host:{game_port}")
                        
                    first_device = devices[0]
                    # Lấy tên thân thiện
                    friendly = first_device
                    try:
                        ld_out = subprocess.run([r'E:\LDPlayer\LDPlayer9\ldconsole.exe', 'list2'], capture_output=True, text=True).stdout
                        for line in ld_out.splitlines():
                            parts = line.split(',')
                            if len(parts) >= 2:
                                idx = int(parts[0])
                                name = parts[1]
                                adb_port = 5554 + (idx * 2)
                                if f'emulator-{adb_port}' == first_device:
                                    friendly = name
                                    break
                    except Exception:
                        pass
                        
                    print(f"🔧 Đang dùng giả lập [\033[96m{friendly}\033[0m] để lấy tọa độ...")
                    adb = ADBController(device_name=first_device)
                    adb.connect()
                    calibrate_interactive(adb)
            elif sub_choice == "2":
                print("🚀 Khởi chạy kịch bản đa luồng ADB...")
                # We will import adb_script_runner and launch it!
                try:
                    from modules.adb_script_runner import ScriptRunnerThread, GlobalProxyHub
                    import threading
                    import subprocess
                    
                    # Tự động lấy danh sách device đang bật từ ADB
                    print("🔍 Đang tìm các giả lập LDPlayer đang chạy...")
                    
                    # Lấy tên thân thiện từ ldconsole
                    friendly_names = {}
                    try:
                        ld_out = subprocess.run([r'E:\LDPlayer\LDPlayer9\ldconsole.exe', 'list2'], capture_output=True, text=True).stdout
                        for line in ld_out.splitlines():
                            parts = line.split(',')
                            if len(parts) >= 2:
                                idx = int(parts[0])
                                name = parts[1]
                                adb_port = 5554 + (idx * 2)
                                friendly_names[f'emulator-{adb_port}'] = name
                    except:
                        pass

                    out = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', 'devices'], capture_output=True, text=True).stdout
                    devices = []
                    display_devices = []
                    for line in out.splitlines()[1:]:
                        if 'device' in line and 'offline' not in line:
                            dev = line.split()[0]
                            devices.append(dev)
                            friendly = friendly_names.get(dev, dev)
                            display_devices.append(friendly)
                            
                    num_emus = len(devices)
                    if num_emus == 0:
                        print("\033[91m[LỖI]\033[0m Không tìm thấy giả lập nào đang chạy! Vui lòng mở LDPlayer.")
                        continue
                        
                    print(f"✅ Tìm thấy {num_emus} giả lập: {display_devices}")
                    
                    # Khởi động GlobalProxyHub duy nhất tại cổng 10002
                    hub = GlobalProxyHub.get_instance()
                    hub.listen_port = 10002
                    hub.start()
                    safe_print("✅ Đã khởi chạy Global Proxy Hub tại 0.0.0.0:10002")
                    # Quét danh sách kịch bản trong thư mục scripts/ (tự động kiểm tra cả Tool/scripts và scripts/)
                    tool_dir = os.path.dirname(os.path.abspath(__file__))
                    search_dirs = [
                        os.path.join(tool_dir, "scripts"),
                        os.path.abspath("scripts"),
                        os.path.join(os.path.dirname(tool_dir), "scripts")
                    ]
                    available_scripts = []
                    seen_files = set()
                    for s_dir in search_dirs:
                        if os.path.exists(s_dir):
                            for fname in sorted(os.listdir(s_dir)):
                                if fname.endswith(".json") and fname not in seen_files:
                                    fpath = os.path.join(s_dir, fname)
                                    try:
                                        with open(fpath, "r", encoding="utf-8") as f:
                                            sdata = json.load(f)
                                        title = sdata.get("title", fname) if isinstance(sdata, dict) else fname
                                        steps_cnt = len(sdata.get("steps", [])) if isinstance(sdata, dict) else (len(sdata) if isinstance(sdata, list) else 0)
                                        max_lvl = sdata.get("max_level") if isinstance(sdata, dict) else None
                                        if max_lvl is None and ("12" in title.lower() or "slot_01" in fname.lower()):
                                            max_lvl = 15
                                        available_scripts.append({
                                            "filename": fname,
                                            "path": fpath,
                                            "title": title,
                                            "steps": steps_cnt,
                                            "max_level": max_lvl
                                        })
                                        seen_files.add(fname)
                                    except Exception:
                                        pass

                    if not available_scripts:
                        print("\033[91m[LỖI]\033[0m Chưa có kịch bản nào trong thư mục 'scripts/'. Vui lòng dùng Option 11 để ghi kịch bản trước!")
                        continue

                    # Sắp xếp ưu tiên các slot_01.json lên đầu để tiện nhấn Enter chọn ngay
                    available_scripts.sort(key=lambda s: (not s["filename"].startswith("slot_"), s["filename"]))

                    print("\n" + "=" * 90)
                    print("                     DANH SÁCH KỊCH BẢN TỰ ĐỘNG (SCRIPTS)")
                    print("=" * 90)
                    print(f" {'STT':<4} | {'TÊN KỊCH BẢN':<24} | {'SỐ BƯỚC':<9} | {'ĐIỀU KIỆN':<16} | {'TỆP TIN':<24}")
                    print("-" * 90)
                    for idx, sinfo in enumerate(available_scripts):
                        cond_text = f"\033[93mLv < {sinfo['max_level']}\033[0m" if sinfo.get("max_level") else "Không giới hạn"
                        print(f" [{idx + 1:<2}] | {sinfo['title'][:22]:<24} | {sinfo['steps']:<9} | {cond_text:<25} | {sinfo['filename']:<24}")
                    print("=" * 90)

                    choice_idx = input(f"-> Chọn kịch bản muốn chạy [1-{len(available_scripts)}] (Mặc định: 1): ").strip()
                    selected_idx = int(choice_idx) - 1 if (choice_idx.isdigit() and 1 <= int(choice_idx) <= len(available_scripts)) else 0
                    chosen_script = available_scripts[selected_idx]
                    cond_info = f" (Điều kiện: \033[93mLevel < {chosen_script['max_level']}\033[0m)" if chosen_script.get("max_level") else ""
                    print(f"-> Đã chọn kịch bản: \033[92m{chosen_script['title']}\033[0m{cond_info} ({chosen_script['filename']} - {chosen_script['steps']} bước)")

                    slot_opt = input("-> Chạy cho Slot nhân vật nào? [all (1->2->3), 1, 2, 3] (Mặc định: all): ").strip().lower()
                    if slot_opt in ["1", "2", "3"]:
                        target_slots = [int(slot_opt)]
                    else:
                        target_slots = [1, 2, 3]
                    print(f"-> Áp dụng cho: \033[96m{'Tất cả các Slot (1->2->3) thỏa điều kiện Level < 15' if len(target_slots) == 3 else f'Slot Nhân Vật #{target_slots[0]} (tự nhảy sang slot khác nếu >= 15)'}\033[0m")

                    # Phân chia tài khoản theo kiểu xoay vòng (Round-Robin)
                    # VD: Giả lập 1 chạy Acc 1,4,7; Giả lập 2 chạy Acc 2,5,8...
                    
                    print("\n" + "=" * 80)
                    print("                PHÂN CHIA TÀI KHOẢN CHO TỪNG GIẢ LẬP (XOAY VÒNG)")
                    print("=" * 80)

                    runners = []
                    emu_lists = [[] for _ in range(num_emus)]
                    for idx, acc in enumerate(active_accounts):
                        emu_lists[idx % num_emus].append(acc)

                    for i in range(num_emus):
                        emu_accs = emu_lists[i]
                        
                        if not emu_accs:
                            continue
                            
                        device_name = devices[i]
                        dev_label = display_devices[i] if i < len(display_devices) else device_name
                        proxy_port = 10001 + i * 2
                        
                        acc_indices = [active_accounts.index(a) + 1 for a in emu_accs]
                        if len(acc_indices) > 3:
                            seq_str = f"{acc_indices[0]}, {acc_indices[1]}, ..., {acc_indices[-1]}"
                        else:
                            seq_str = ", ".join(map(str, acc_indices))
                            
                        first_acc = emu_accs[0][0] if isinstance(emu_accs[0], (list, tuple)) else str(emu_accs[0])
                        print(f"  • Giả lập {i+1} [Máy ảo: \033[96m{dev_label}\033[0m | Port {proxy_port}]: \033[92m{len(emu_accs)}\033[0m acc (Các Acc: \033[93m{seq_str}\033[0m - Bắt đầu từ '{first_acc}')")
                        
                        runner = ScriptRunnerThread(emu_id=i+1, device_name=device_name, listen_port=proxy_port)
                        runner.account_list = emu_accs
                        runner.target_slots = target_slots
                        runner.load_script(chosen_script["path"])
                        
                        runners.append(runner)
                    print("=" * 80)
                    
                    print(f"\n🚀 Đang khởi động {len(runners)} luồng giả lập...")
                    
                    for r in runners:
                        threading.Thread(target=r.run_automation_loop, daemon=True).start()
                        
                    print("\n\033[92mHỆ THỐNG ĐÃ KHỞI CHẠY!\033[0m")
                    print("Nhập 'pause <id>', 'resume <id>', 'back <id>' để điều khiển.")
                    print("Nhập 'quit' để dừng toàn bộ và thoát.")
                    
                    while True:
                        cmd = input("ADB-Console> ").strip().lower()
                        if cmd == "quit":
                            GlobalProxyHub.get_instance().stop()
                            for r in runners:
                                r.stop()
                            break
                        parts = cmd.split()
                        if len(parts) == 2:
                            action, eid = parts[0], parts[1]
                            if eid.isdigit():
                                eid = int(eid)
                                target_runner = next((r for r in runners if r.emu_id == eid), None)
                                if target_runner:
                                    if action == "pause": target_runner.pause()
                                    elif action == "resume": target_runner.resume()
                                    elif action == "back": target_runner.back()
                                    else: print("Lệnh không hợp lệ.")
                                else:
                                    print(f"Không tìm thấy giả lập ID {eid}")
                        
                except Exception as e:
                    print(f"\033[91m[LỖI]\033[0m {e}")
                    
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass

        elif choice == "12":
            if not active_accounts:
                print(f"\033[91m[LỖI]\033[0m File '{settings.accounts_file_basename}' không có tài khoản nào!")
                continue

            print("\n" + "=" * 78)
            print("        🎁 TREO GAME ONLINE & TỰ ĐỘNG NHẬN QUÀ ONLINE (AFK KEEP-ALIVE)")
            print("=" * 78)
            first_acc = active_accounts[0][0] if isinstance(active_accounts[0], (list, tuple)) else str(active_accounts[0])
            last_acc = active_accounts[-1][0] if isinstance(active_accounts[-1], (list, tuple)) else str(active_accounts[-1])
            print(f"-> Tổng tài khoản đang chọn: \033[92m{len(active_accounts)}\033[0m (Từ '{first_acc}' đến '{last_acc}')")
            print("-> Chọn chế độ treo nhận quà:")
            print("  [1] \033[92m🔄 Tự Động Luân Chuyển Nhân Vật\033[0m: Hết quà ở NV này -> Tự đổi sang NV tiếp theo")
            print("      (Treo toàn bộ nhân vật trên tài khoản đến khi nhận hết quà trong ngày)")
            print("  [2] \033[96m🎯 Cố định 1 Nhân Vật\033[0m (Chỉ treo 1 slot cố định: Slot 1, 2, 3 hoặc Level cao nhất)")
            print("  [0] Quay lại Menu chính")
            print("-" * 78)
            mode_input = input("-> Chọn chế độ [1-2] (Enter = 1 - Tự động luân chuyển): ").strip()
            if mode_input == "0":
                continue

            auto_cycle = True
            slot_choice = 1

            if mode_input == "2":
                auto_cycle = False
                print("\n-> Chọn vị trí nhân vật muốn cố định treo:")
                print("   [1] Nhân vật Slot 1 (Mặc định)")
                print("   [2] Nhân vật Slot 2")
                print("   [3] Nhân vật Slot 3")
                print("   [4] Nhân vật có Level cao nhất")
                s_inp = input("   -> Chọn Slot [1-4] (Enter=1): ").strip()
                slot_choice = int(s_inp) if s_inp in ("1", "2", "3", "4") else 1

            # Hỏi số luồng chạy đồng thời
            default_workers = min(len(active_accounts), settings.max_concurrent_accounts)
            print(f"\n-> Số tài khoản online đồng thời (Số luồng) [1-{len(active_accounts)}] (Mặc định: {default_workers}): ", end="")
            workers_inp = input().strip()
            workers = int(workers_inp) if (workers_inp.isdigit() and int(workers_inp) > 0) else default_workers

            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_online_reward(
                proxy_accounts,
                auto_cycle_characters=auto_cycle,
                slot_choice=slot_choice,
                max_workers=workers
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
        elif choice == "13":
            # 1. Prepare server configurations
            servers_config = {
                "accounts_tuyet_vo_than.txt": None,
                "accounts_phung_thien.txt": None
            }
            
            # Find the actual game hosts/ports from settings
            for srv in settings.servers:
                acc_file = srv.get("accounts_file") or slugify_server_name(srv.get("name", ""))
                if acc_file in servers_config:
                    servers_config[acc_file] = srv
            
            multi_accounts = []
            
            # 2. Load accounts from both files and enrich with server config
            for acc_file, srv_conf in servers_config.items():
                if not srv_conf:
                    continue
                acc_list = load_accounts(acc_file)
                if not acc_list:
                    continue
                    
                g_host = srv_conf.get("game_host", "127.0.0.1")
                g_port = srv_conf.get("game_port", 10000)
                m_host = srv_conf.get("master_host", g_host)
                m_port = srv_conf.get("master_port", 30000)
                s_name = srv_conf.get("name", "Unknown Server")
                
                # proxy_accounts will assign proxies just like normal
                acc_list = FreeProxyManager.assign_proxies_to_accounts(acc_list)
                
                for a in acc_list:
                    username = a[0]
                    password = a[1]
                    proxy = a[2] if len(a) >= 3 else None
                    # Append enriched tuple
                    multi_accounts.append((username, password, proxy, g_host, g_port, m_host, m_port, s_name))
            
            if not multi_accounts:
                print("\033[91m[LỖI]\033[0m Không tìm thấy tài khoản nào trong cả 2 file accounts_tuyet_vo_than.txt và accounts_phung_thien.txt!")
                continue
                
            print("\n" + "=" * 80)
            print("   🎁 TREO GAME ONLINE CÙNG LÚC 2 SERVER (TUYỆT VÔ THẦN & PHỤNG THIÊN)")
            print("=" * 80)
            print(f"-> Đã tải tổng cộng \033[92m{len(multi_accounts)}\033[0m tài khoản từ 2 Server.")
            print("-> Chọn chế độ treo nhận quà:")
            print("  [1] \033[92m🔄 Tự Động Luân Chuyển Nhân Vật\033[0m: Hết quà ở NV này -> Tự đổi sang NV tiếp theo")
            print("  [2] \033[96m🎯 Cố định 1 Nhân Vật\033[0m (Chỉ treo 1 slot cố định: Slot 1, 2, 3 hoặc Level cao nhất)")
            print("  [0] Quay lại Menu chính")
            print("-" * 80)
            mode_input = input("-> Chọn chế độ [1-2] (Enter = 1 - Tự động luân chuyển): ").strip()
            if mode_input == "0":
                continue

            auto_cycle = True
            slot_choice = 1

            if mode_input == "2":
                auto_cycle = False
                print("\n-> Chọn vị trí nhân vật muốn cố định treo:")
                print("   [1] Nhân vật Slot 1 (Mặc định)")
                print("   [2] Nhân vật Slot 2")
                print("   [3] Nhân vật Slot 3")
                print("   [4] Nhân vật có Level cao nhất")
                s_inp = input("   -> Chọn Slot [1-4] (Enter=1): ").strip()
                slot_choice = int(s_inp) if s_inp in ("1", "2", "3", "4") else 1

            default_workers = min(len(multi_accounts), settings.max_concurrent_accounts * 2)
            print(f"\n-> Số tài khoản online đồng thời (Số luồng) [1-{len(multi_accounts)}] (Mặc định: {default_workers}): ", end="")
            workers_inp = input().strip()
            workers = int(workers_inp) if (workers_inp.isdigit() and int(workers_inp) > 0) else default_workers

            run_all_online_reward(
                multi_accounts,
                auto_cycle_characters=auto_cycle,
                slot_choice=slot_choice,
                max_workers=workers
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
                
        elif choice == "14":
            print("\n-> Bắt đầu tiến trình sử dụng Vé EXP Nhân Vật (Khóa) hàng loạt...")
            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_bag_cleaner(
                proxy_accounts,
                max_workers=settings.max_concurrent_accounts
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
                
        elif choice == "7":
            print("\n-> CẤU HÌNH GỬI TOÀN BỘ BẠC CHO NGƯỜI NHẬN")
            try:
                input_target = input("-> Người nhận (Tên / ID): ").strip()
                if not input_target:
                    print("[HỦY THAO TÁC] Đã quay lại Menu chính.")
                    continue
            except (KeyboardInterrupt, EOFError):
                print("\n[HỦY THAO TÁC] Đã quay lại Menu chính.")
                continue

            from modules.silver_sender import run_test_silver_mail, run_all_silver_sender
            
            skip_test = False
            try:
                ask_test = input("-> Bạn có muốn gửi 1 Bạc TEST trước để kiểm tra không? (Chọn N để BỎ QUA test và gửi hàng loạt luôn) [Y/n]: ").strip().lower()
                if ask_test in ["n", "no"]:
                    skip_test = True
            except (KeyboardInterrupt, EOFError):
                pass
                
            if not skip_test:
                print(f"\n-> Đang tìm và gửi thư TEST 1 Bạc đến '{input_target}' để kiểm tra (Có thể mất thời gian nếu các acc đầu không có bạc)...")
                if run_test_silver_mail(active_accounts, input_target):
                    try:
                        confirm = input(f"\n-> \033[93m[QUAN TRỌNG]\033[0m Vui lòng vào game kiểm tra xem nhân vật '{input_target}' ĐÃ NHẬN ĐƯỢC THƯ TEST CHƯA?\n   Nếu ĐÚNG, gõ 'Y' (hoặc nhấn Enter) để Bắt đầu gửi hàng loạt.\n   Nếu SAI, gõ 'N' để Hủy bỏ.\n-> [Y/n]: ").strip().lower()
                        if confirm in ["n", "no"]:
                            print("[HỦY THAO TÁC] Đã hủy gửi hàng loạt.")
                            continue
                    except (KeyboardInterrupt, EOFError):
                        print("\n[HỦY THAO TÁC] Đã quay lại Menu chính.")
                        continue
                else:
                    try:
                        confirm = input(f"\n-> \033[91m[LỖI]\033[0m Không thể gửi thư Test (do các acc đầu không có bạc hoặc lỗi mạng).\n   Bạn có muốn TIẾP TỤC gửi hàng loạt luôn không?\n-> [Y/n]: ").strip().lower()
                        if confirm in ["n", "no"]:
                            continue
                    except (KeyboardInterrupt, EOFError):
                        continue
                        
            limit_str = ""
            try:
                limit_str = input("-> Nhập số lượng thư tối đa muốn gửi (Nhấn Enter để gửi TẤT CẢ): ").strip()
            except (KeyboardInterrupt, EOFError):
                pass
            max_mails = int(limit_str) if limit_str.isdigit() and int(limit_str) > 0 else None

            proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(active_accounts)
            run_all_silver_sender(
                proxy_accounts,
                target=input_target,
                max_workers=settings.max_concurrent_accounts,
                max_mails=max_mails
            )
            try:
                input("\nNhấn Enter để quay lại Menu...")
            except (KeyboardInterrupt, EOFError):
                pass
                
        else:
            print("\033[91m[LỖI]\033[0m Lựa chọn không hợp lệ. Vui lòng chọn từ 0 đến 19, hoặc C.")


def force_exit_handler(sig, frame):
    import os
    print("\n\033[93m[ĐÃ DỪNG]\033[0m Hủy tiến trình bởi người dùng (Đang đăng xuất toàn bộ acc...). Tạm biệt!\n", flush=True)
    try:
        from core.network import graceful_shutdown_all
        graceful_shutdown_all()
    except Exception:
        pass
    os._exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, force_exit_handler)
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        force_exit_handler(None, None)
