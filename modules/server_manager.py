"""
PVTK Server Selection & Multi-Server Manager Module
Allows users to switch between game servers (e.g. Tuyệt Vô Thần, Phụng Thiên, Chín Tầng Mây, Kỳ Thương, Đấu Trường),
auto-fetch live servers from game master (Opcode 0x138F), isolate account files per server, test ping, and persist configurations.
"""

import sys
import os
import time
import socket
from typing import Dict, Any, Tuple, List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings, slugify_server_name
from core.network import PVTKConnection
from core.message import Message
import core.protocol as proto
from core.device_faker import DeviceFaker


def count_accounts_in_file(filename: str) -> int:
    """Counts valid account entries in a given accounts file."""
    path = os.path.join(settings.base_dir, filename)
    if not os.path.exists(path):
        return 0
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "|" in line:
                    count += 1
    except Exception:
        pass
    return count


def test_server_ping(host: str, port: int, timeout: float = 2.0) -> Tuple[bool, float]:
    """Tests TCP socket connection to server host:port and measures round-trip ping in milliseconds."""
    try:
        start = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return True, round(elapsed_ms, 1)
    except Exception:
        return False, 0.0


def fetch_live_servers_from_master(accounts_file: str = "accounts.txt") -> List[Dict[str, Any]]:
    """
    Connects to Master Gateway (0x1388 & 0x138A) and requests live Server List (0x138F).
    Parses live areas and lines directly from server memory.
    """
    username = "knbpv1"
    password = "1"
    if os.path.exists(accounts_file):
        with open(accounts_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "|" in line:
                    username, password = line.split("|", 1)
                    break

    m_host = settings.master_host
    m_port = settings.master_port

    conn = PVTKConnection(m_host, m_port, timeout=8.0)
    if not conn.connect():
        return []

    try:
        dev = DeviceFaker.get_device_fingerprint(username)
        # 1. Version Check (0x1388)
        msg_ver = Message(proto.LC_CHECKEDITION if hasattr(proto, "LC_CHECKEDITION") else 0x1388)
        msg_ver.putShort(1000)
        msg_ver.putInt(proto.CLIENT_BUILD_VERSION)
        msg_ver.putInt(111)
        msg_ver.putByte(5)
        msg_ver.putString("")
        msg_ver.putByte(0)
        conn.send_message(msg_ver)
        res_ver = conn.receive_message(timeout=5.0)

        # 2. Login (0x138A)
        msg_login = Message(proto.LC_PLAYER_LOGIN)
        msg_login.putString(username)
        msg_login.putString(password)
        msg_login.putString(dev["client_ip"])
        msg_login.putString(dev["os"])
        msg_login.putString(dev["handset"])
        conn.send_message(msg_login)
        res_login = conn.receive_message(timeout=5.0)
        if not res_login or res_login.getByte() != 0:
            conn.close()
            return []

        # 3. Server List (0x138F)
        msg_servers = Message(0x138F)
        conn.send_message(msg_servers)
        res_server = conn.receive_message(timeout=6.0)
        if not res_server:
            conn.close()
            return []

        parsed_servers = []
        area_size = res_server.getByte()
        for a in range(area_size):
            a_id = res_server.getShort()
            a_name = res_server.getString()
            a_count = res_server.getByte()
            a_state = res_server.getByte()
            a_state_str = res_server.getString()
            a_tag = res_server.getString()
            a_code = res_server.getBoolean()
            line_size = res_server.getByte()
            for l in range(line_size):
                l_id = res_server.getShort()
                l_name = res_server.getString()
                sock_url = res_server.getString()
                wap_url = res_server.getString()
                http_url = res_server.getString()
                l_state = res_server.getByte()
                l_state_str = res_server.getString() if res_server.hasRemaining() else ""

                clean_sock = sock_url.replace("socket://", "").replace("http://", "").strip()
                if ":" in clean_sock:
                    g_host, g_port_str = clean_sock.split(":", 1)
                    g_port = int(g_port_str)
                else:
                    g_host = clean_sock
                    g_port = 10000

                server_title = f"{a_name} (Cụm {a_id})"
                acc_file = slugify_server_name(server_title)
                parsed_servers.append({
                    "name": server_title,
                    "game_host": g_host,
                    "game_port": g_port,
                    "master_host": m_host,
                    "master_port": m_port,
                    "accounts_file": acc_file,
                    "desc": f"Line {l_id}: {l_name} | Trạng thái: {l_state_str or a_state_str}"
                })

        conn.close()
        return parsed_servers
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return []


def run_server_manager_cli():
    """Interactive CLI menu to select, view, add, and test game servers."""
    while True:
        settings.reload()
        servers = settings.servers
        current_idx = settings.selected_server_index

        print("\n" + "=" * 90)
        print("                  DANH SÁCH MÁY CHỦ PVTK & FILE TÀI KHOẢN RIÊNG")
        print("=" * 90)
        print(f"{'STT':<4} | {'TÊN MÁY CHỦ':<24} | {'GAME SERVER':<20} | {'FILE ACC (SỐ LƯỢNG)':<26} | {'TRẠNG THÁI'}")
        print("-" * 90)

        for i, srv in enumerate(servers):
            name = srv.get("name", f"Server {i+1}")
            g_host = srv.get("game_host", "127.0.0.1")
            g_port = srv.get("game_port", 10000)
            conn_str = f"{g_host}:{g_port}"
            
            acc_file = srv.get("accounts_file") or slugify_server_name(name)
            acc_count = count_accounts_in_file(acc_file)
            acc_display = f"{acc_file} ({acc_count} acc)"

            is_active = (i == current_idx)
            star = "★" if is_active else " "
            status_tag = "\033[92m● ĐANG CHỌN\033[0m" if is_active else "\033[90m(Sẵn sàng)\033[0m"

            print(f"  [{i+1}] {star} {name:<26} | {conn_str:<20} | {acc_display:<35} | {status_tag}")

        print("------------------------------------------------------------------------------------------")
        print("  [1 - %d] Nhập số thứ tự để CHỌN máy chủ" % len(servers))
        print("  [F]      Đổi / Gán File Tài Khoản cho Máy Chủ đang chọn")
        print("  [R]      TỰ ĐỘNG TẢI LẠI (Quét danh sách Server thực tế từ Game)")
        print("  [T]      Kiểm tra tốc độ kết nối (Ping / Test Online) tất cả Server")
        print("  [A]      Thêm Máy Chủ Mới (Custom IP & Port)")
        print("  [D]      Xóa Máy Chủ")
        print("  [0]      Quay lại Menu chính")
        print("=" * 90)

        try:
            choice = input("-> Vui lòng chọn thao tác: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not choice or choice == "0":
            break

        # Check numeric selection
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                selected_srv = servers[idx]
                settings.select_server(idx)
                print(f"\n\033[92m[THÀNH CÔNG]\033[0m Đã chuyển sang máy chủ: \033[93m'{selected_srv['name']}'\033[0m ({selected_srv['game_host']}:{selected_srv['game_port']})")
                print(f"  -> Đang nạp danh sách tài khoản từ file: \033[96m{settings.accounts_file_basename}\033[0m")
                time.sleep(0.7)
                break
            else:
                print(f"\033[91m[LỖI]\033[0m Số thứ tự máy chủ không hợp lệ (1 - {len(servers)}).")
                continue

        # Change/Assign Accounts File for Current Server
        if choice.upper() == "F":
            active_srv = servers[current_idx]
            curr_file = active_srv.get("accounts_file", slugify_server_name(active_srv.get("name", "")))
            print("\n" + "=" * 70)
            print(f"       GÁN FILE TÀI KHOẢN CHO MÁY CHỦ: {active_srv.get('name')}")
            print("=" * 70)
            print(f"-> File tài khoản hiện tại: \033[96m{curr_file}\033[0m")
            try:
                new_file = input("-> Nhập tên file tài khoản mới (Ví dụ: accounts_phung_thien.txt hoặc accounts.txt): ").strip()
                if new_file:
                    if not new_file.endswith(".txt") and not new_file.endswith(".csv"):
                        new_file += ".txt"
                    settings.set_server_accounts_file(current_idx, new_file)
                    print(f"\033[92m[THÀNH CÔNG]\033[0m Đã gán file tài khoản '\033[93m{new_file}\033[0m' cho máy chủ '{active_srv.get('name')}'.")
                    time.sleep(0.8)
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        # Live Refresh from Master Server (0x138F)
        if choice.upper() == "R":
            print("\n-> Đang kết nối tới Master Gateway và quét danh sách máy chủ thời gian thực...")
            live_srvs = fetch_live_servers_from_master()
            if live_srvs:
                settings.servers = live_srvs
                settings.save_json()
                print(f"\033[92m[THÀNH CÔNG]\033[0m Đã cập nhật thành công {len(live_srvs)} máy chủ thực tế từ Game!")
                for s in live_srvs:
                    print(f"  -> \033[96m{s['name']}\033[0m: {s['game_host']}:{s['game_port']} | File: {s['accounts_file']}")
                time.sleep(1.0)
            else:
                print("\033[91m[THẤT BẠI]\033[0m Không thể tải danh sách server từ Master Gateway.")
                time.sleep(1.0)
            continue

        # Ping Test
        if choice.upper() == "T":
            print("\n-> Đang kiểm tra kết nối đến tất cả máy chủ...")
            print("-" * 75)
            for i, srv in enumerate(servers, 1):
                name = srv.get("name", f"Server {i}")
                g_host = srv.get("game_host", "")
                g_port = srv.get("game_port", 10000)
                ok, ping = test_server_ping(g_host, g_port)
                if ok:
                    ping_colored = f"\033[92mONLINE (Ping: {ping:.1f}ms)\033[0m"
                else:
                    ping_colored = "\033[91mOFFLINE (Không kết nối được)\033[0m"
                print(f"[{i}] {name:<26} ({g_host}:{g_port:<5}) -> {ping_colored}")
            print("-" * 75)
            try:
                input("Nhấn Enter để tiếp tục...")
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        # Add Server
        if choice.upper() == "A":
            print("\n" + "=" * 70)
            print("                 THÊM MÁY CHỦ MỚI (ADD SERVER)")
            print("=" * 70)
            try:
                s_name = input("-> Tên Máy Chủ (Ví dụ: Tuyệt Vô Thần S2): ").strip()
                if not s_name:
                    print("\033[93m[ĐÃ HỦY]\033[0m Tên máy chủ không được để trống.")
                    continue

                s_host = input("-> Địa chỉ IP Game Server (Ví dụ: 123.30.142.210): ").strip()
                if not s_host:
                    print("\033[93m[ĐÃ HỦY]\033[0m IP máy chủ không được để trống.")
                    continue

                s_port_str = input("-> Cổng Game Server [Mặc định: 10000]: ").strip()
                s_port = int(s_port_str) if s_port_str else 10000

                m_host = input("-> Địa chỉ IP Master Gateway [Mặc định: 123.30.142.219]: ").strip()
                if not m_host:
                    m_host = "123.30.142.219"

                m_port_str = input("-> Cổng Master Gateway [Mặc định: 30000]: ").strip()
                m_port = int(m_port_str) if m_port_str else 30000

                s_acc_file = input("-> File tài khoản riêng [Mặc định: tự tạo theo tên server]: ").strip()
                s_desc = input("-> Ghi chú (tùy chọn): ").strip()

                new_idx = settings.add_server(s_name, s_host, s_port, m_host, m_port, s_desc, accounts_file=s_acc_file)
                print(f"\n\033[92m[THÀNH CÔNG]\033[0m Đã thêm máy chủ '{s_name}' vào danh sách!")
                
                switch_now = input("-> Bạn có muốn chuyển sang máy chủ này luôn không? (y/n) [y]: ").strip().lower()
                if switch_now in ["", "y", "yes", "c", "co", "1"]:
                    settings.select_server(new_idx)
                    print(f"\033[92m[ĐÃ CHỌN]\033[0m Đã kích hoạt máy chủ '{s_name}'.")
                    time.sleep(0.7)
                    break
            except (KeyboardInterrupt, EOFError):
                print("\n[ĐÃ HỦY]")
            continue

        # Delete Server
        if choice.upper() == "D":
            print("\n" + "=" * 70)
            print("                 XÓA MÁY CHỦ (DELETE SERVER)")
            print("=" * 70)
            if len(servers) <= 1:
                print("\033[93m[CẢNH BÁO]\033[0m Cần duy trì ít nhất 1 máy chủ trong danh sách.")
                time.sleep(1.0)
                continue

            for i, srv in enumerate(servers, 1):
                print(f"  [{i}] {srv.get('name')} ({srv.get('game_host')}:{srv.get('game_port')})")

            try:
                del_str = input(f"-> Nhập số thứ tự máy chủ muốn xóa [1-{len(servers)}] (Nhấn Enter để hủy): ").strip()
                if del_str.isdigit():
                    del_idx = int(del_str) - 1
                    if 0 <= del_idx < len(servers):
                        del_name = servers[del_idx].get("name", "")
                        settings.delete_server(del_idx)
                        print(f"\033[92m[THÀNH CÔNG]\033[0m Đã xóa máy chủ '{del_name}'.")
                        time.sleep(1.0)
                    else:
                        print("\033[91m[LỖI]\033[0m Số thứ tự không hợp lệ.")
            except (KeyboardInterrupt, EOFError):
                pass
            continue
