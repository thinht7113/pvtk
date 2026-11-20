"""
PVTK Latest Character ID Tracker & Sniffer Module
Helps track the most recently created character ID on the server in real-time,
enabling players to monitor and target 'ID số đẹp' (beautiful character IDs).
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

from core.config import settings
from core.message import Message
from core.network import PVTKConnection
from core.device_faker import DeviceFaker
from modules.auth import AuthManager
from modules.mail_sender import parse_player_id

STORAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "latest_character_id.json")


class IDTracker:
    @staticmethod
    def load_latest_stored_id() -> Optional[Dict[str, Any]]:
        """Loads the last saved latest character ID from storage."""
        if not os.path.exists(STORAGE_FILE):
            return None
        try:
            with open(STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data and "seq_num" in data:
                    return data
        except Exception:
            pass
        return None

    @staticmethod
    def save_latest_id(prefix: str, seq_num: int, pid: int, name: str, level: int) -> Dict[str, Any]:
        """Saves newly discovered latest character ID to storage."""
        os.makedirs(os.path.dirname(STORAGE_FILE), exist_ok=True)
        data = {
            "prefix": prefix,
            "seq_num": seq_num,
            "uid": f"{prefix}{seq_num}",
            "pid": pid,
            "name": name,
            "level": level,
            "last_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(STORAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CẢNH BÁO] Lỗi lưu ID mới nhất: {e}")
        return data

    @staticmethod
    def parse_prefix_and_seq(target_str: str) -> Tuple[str, int, int]:
        """
        Parses 'am6281291' into prefix ('am'), seq_num (6281291), and full numeric pid (1906281291).
        """
        s = str(target_str).strip().lower()
        pid = parse_player_id(s)
        if pid is None:
            raise ValueError(f"Không thể phân tích ID: '{target_str}'")

        prefix = ""
        for ch in s:
            if ch.isalpha():
                prefix += ch
            else:
                break
        seq_num = int(s[len(prefix):]) if prefix else pid % 50000000
        return prefix, seq_num, pid

    @staticmethod
    def make_uid_and_pid(prefix: str, seq_num: int) -> Tuple[str, int]:
        uid = f"{prefix}{seq_num}"
        pid = parse_player_id(uid)
        return uid, pid

    @staticmethod
    def query_player_by_pid(conn: PVTKConnection, pid: int, timeout: float = 1.8) -> Optional[Dict[str, Any]]:
        """Queries character info by PID using Opcode 0x34DC (CG_ACTOR_SEE)."""
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(0x34DC)
            msg.putInt(pid)
            res = conn.send_and_wait(msg, expected_type=0x34DC, timeout=timeout)
            if not res:
                return None

            is_online = res.getBoolean()
            p_id = res.getInt()
            name = res.getString() if res.hasRemaining() else ""
            lvl = res.getByte() if res.hasRemaining() else 0

            if p_id > 0 and name:
                return {
                    "id": p_id,
                    "name": name,
                    "level": lvl,
                    "is_online": is_online
                }
            return None
        except Exception:
            return None

    @staticmethod
    def scan_latest_id(conn: PVTKConnection, start_uid: str, max_lookahead_misses: int = 5, verbose: bool = True) -> Optional[Dict[str, Any]]:
        """
        Scans sequentially from start_uid onwards to find the highest registered character ID on the server.
        """
        prefix, start_seq, start_pid = IDTracker.parse_prefix_and_seq(start_uid)
        
        if verbose:
            print(f"\n[TIẾN TRÌNH] Bắt đầu quét từ mốc ID: \033[93m{prefix}{start_seq}\033[0m (PID: {start_pid})...")

        latest_found = None
        curr_seq = start_seq

        # 1. Verify start position
        _, curr_pid = IDTracker.make_uid_and_pid(prefix, curr_seq)
        info = IDTracker.query_player_by_pid(conn, curr_pid)
        if info:
            latest_found = {
                "prefix": prefix,
                "seq_num": curr_seq,
                "uid": f"{prefix}{curr_seq}",
                "pid": curr_pid,
                "name": info["name"],
                "level": info["level"]
            }
            if verbose:
                print(f"  \033[92m[TỒN TẠI]\033[0m {prefix}{curr_seq} -> Tên: '{info['name']}' (Lv {info['level']})")
        else:
            # If start_seq does not exist, step backward up to 20 IDs to locate the active frontier
            if verbose:
                print(f"  \033[90m[CHƯA TỒN TẠI]\033[0m {prefix}{curr_seq}. Đang dò lùi lại tìm mốc gần nhất...")
            back_steps = 0
            while back_steps < 30:
                curr_seq -= 1
                back_steps += 1
                _, b_pid = IDTracker.make_uid_and_pid(prefix, curr_seq)
                b_info = IDTracker.query_player_by_pid(conn, b_pid)
                if b_info:
                    latest_found = {
                        "prefix": prefix,
                        "seq_num": curr_seq,
                        "uid": f"{prefix}{curr_seq}",
                        "pid": b_pid,
                        "name": b_info["name"],
                        "level": b_info["level"]
                    }
                    if verbose:
                        print(f"  \033[92m[TỒN TẠI GẦN NHẤT]\033[0m {prefix}{curr_seq} -> Tên: '{b_info['name']}' (Lv {b_info['level']})")
                    break

        if not latest_found:
            # If we couldn't find any online players nearby, we will assume the starting ID is our baseline!
            if verbose:
                print(f"  \033[93m[CẢNH BÁO]\033[0m Không tìm thấy nhân vật Online nào làm mốc. Tạm dùng mốc khởi tạo: {prefix}{start_seq}")
            latest_found = {
                "prefix": prefix,
                "seq_num": start_seq,
                "uid": f"{prefix}{start_seq}",
                "pid": start_pid,
                "name": "? (Offline)",
                "level": 0
            }
            curr_seq = start_seq

        # 2. Fast forward jump search if gap is large
        step = 100
        while step >= 10:
            jump_seq = curr_seq + step
            _, j_pid = IDTracker.make_uid_and_pid(prefix, jump_seq)
            j_info = IDTracker.query_player_by_pid(conn, j_pid)
            if j_info:
                curr_seq = jump_seq
                latest_found = {
                    "prefix": prefix,
                    "seq_num": curr_seq,
                    "uid": f"{prefix}{curr_seq}",
                    "pid": j_pid,
                    "name": j_info["name"],
                    "level": j_info["level"]
                }
                if verbose:
                    print(f"  \033[96m[BƯỚC NHẢY +{step}]\033[0m Tìm thấy {prefix}{curr_seq} -> '{j_info['name']}'")
            else:
                step = step // 2

        # 3. Fine-grained linear scan to exact boundary
        consecutive_misses = 0
        while True:
            curr_seq += 1
            curr_uid, curr_pid = IDTracker.make_uid_and_pid(prefix, curr_seq)
            info = IDTracker.query_player_by_pid(conn, curr_pid)

            if info:
                latest_found = {
                    "prefix": prefix,
                    "seq_num": curr_seq,
                    "uid": curr_uid,
                    "pid": curr_pid,
                    "name": info["name"],
                    "level": info["level"]
                }
                consecutive_misses = 0
                if verbose:
                    print(f"  \033[92m[TỒN TẠI]\033[0m {curr_uid} -> Tên: '{info['name']}' (Lv {info['level']})")
            else:
                consecutive_misses += 1
                if verbose:
                    print(f"  \033[90m[CHƯA TỒN TẠI]\033[0m {curr_uid}")
                if consecutive_misses >= max_lookahead_misses:
                    break

            time.sleep(0.04)

        if latest_found:
            IDTracker.save_latest_id(
                latest_found["prefix"],
                latest_found["seq_num"],
                latest_found["pid"],
                latest_found["name"],
                latest_found["level"]
            )

        return latest_found


def run_id_tracker_cli(accounts: List[Tuple[str, str, Optional[str]]]):
    """Interactive CLI menu for Option 8: Character ID Sniffer & Tracker."""
    if not accounts:
        print("\033[91m[LỖI]\033[0m Không có tài khoản nào để kết nối kiểm tra!")
        return

    print("\n" + "=" * 80)
    print("        HỆ THỐNG DÒ TÌM ID NHÂN VẬT TẠO MỚI NHẤT SERVER (CANH ID SỐ ĐẸP)")
    print("=" * 80)

    # 1. Check cached latest ID
    cached_data = IDTracker.load_latest_stored_id()
    default_uid = cached_data["uid"] if cached_data else "am6281291"

    if cached_data:
        print(f"-> Mốc ID đã lưu gần nhất: \033[92m{cached_data['uid']}\033[0m (Tên: '{cached_data['name']}' | Cập nhật: {cached_data.get('last_checked_at', '-')})")
    else:
        print(f"-> Chưa có dữ liệu lưu trước đó. Mốc gợi ý mặc định: \033[93m{default_uid}\033[0m")

    print("-" * 80)
    print("  [1] Quét nhanh 1 lần để cập nhật ID mới nhất hiện tại")
    print("  [2] Chế độ theo dõi liên tục (Live Monitor - Báo ngay khi có acc mới tạo)")
    print("  [0] Quay lại Menu chính")
    print("-" * 80)

    try:
        sub_choice = input("-> Vui lòng chọn chế độ [1-2, 0]: ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if sub_choice == "0":
        return

    # Prompt starting ID
    prompt_start = input(f"-> Nhập mốc ID bắt đầu dò (Nhấn Enter để dùng '{default_uid}'): ").strip()
    start_uid = prompt_start if prompt_start else default_uid

    try:
        prefix, start_seq, start_pid = IDTracker.parse_prefix_and_seq(start_uid)
    except Exception as e:
        print(f"\033[91m[LỖI]\033[0m Định dạng ID không hợp lệ: {e}")
        return

    # Optional target ID đẹp
    target_prompt = input("-> Nhập ID số đẹp mong muốn canh (Ví dụ: am6281500, hoặc Enter để bỏ qua): ").strip()
    target_seq = None
    target_uid_str = None
    if target_prompt:
        try:
            _, target_seq, _ = IDTracker.parse_prefix_and_seq(target_prompt)
            target_uid_str = target_prompt
        except Exception:
            target_seq = None

    # Connect using 1st account
    auth_acc = accounts[0]
    u, p = auth_acc[0], auth_acc[1]
    proxy = auth_acc[2] if len(auth_acc) >= 3 else None

    print(f"\n[KẾT NỐI] Đang đăng nhập tài khoản '{u}' để thiết lập kết nối quét...")
    login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, u, p, proxy=proxy)
    if not login_data:
        print(f"\033[91m[LỖI ĐĂNG NHẬP]\033[0m {err}")
        return

    ukey, session_id, login_setting = login_data
    dev = DeviceFaker.get_device_fingerprint(u)
    conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, ukey, session_id, proxy=proxy)
    if not conn:
        print("\033[91m[LỖI KẾT NỐI]\033[0m Không thể kết nối Game Server")
        return

    chars = AuthManager.fetch_character_list(conn)
    if not chars:
        print("\033[91m[LỖI]\033[0m Tài khoản không có nhân vật để vào thế giới")
        conn.close()
        return

    ok = AuthManager.enter_character(conn, chars[0].id, login_setting, device_id=dev["android_id"])
    if not ok:
        print("\033[91m[LỖI]\033[0m Không thể vào nhân vật")
        conn.close()
        return

    print(f"\033[92m[VÀO THÀNH CÔNG]\033[0m Đã vào nhân vật: '{chars[0].name}'. Bắt đầu giao thức dò tìm...")

    if sub_choice == "1":
        # 1. Single Scan
        start_time = time.time()
        res = IDTracker.scan_latest_id(conn, start_uid=start_uid, verbose=True)
        elapsed = time.time() - start_time

        if res:
            print("\n" + "=" * 80)
            print("                 KẾT QUẢ DÒ TÌM ID MỚI NHẤT SERVER")
            print("=" * 80)
            print(f"-> ID Mới Nhất Hiện Tại: \033[92m{res['uid']}\033[0m (PID Số: {res['pid']})")
            print(f"-> Tên Nhân Vật: \033[93m'{res['name']}'\033[0m (Cấp: {res['level']})")
            print(f"-> Số thứ tự nhân vật: \033[96m#{res['seq_num']:,}\033[0m")
            if target_seq:
                diff = target_seq - res['seq_num']
                if diff > 0:
                    print(f"-> Cách ID Số Đẹp '{target_uid_str}': \033[93mCòn {diff} nhân vật nữa\033[0m")
                elif diff == 0:
                    print(f"-> \033[92m[CHÚC MỪNG]\033[0m ĐÃ ĐẠT ĐÚNG ID SỐ ĐẸP '{target_uid_str}'!")
                else:
                    print(f"-> \033[91m[ĐÃ QUA]\033[0m ID số đẹp '{target_uid_str}' đã bị vượt qua {abs(diff)} nhân vật trước.")
            print(f"-> Thời gian quét: {elapsed:.2f} giây (Đã tự động lưu vào storage)")
            print("=" * 80)
        else:
            print("\033[91m[THẤT BẠI]\033[0m Không tìm thấy nhân vật nào hợp lệ.")

    elif sub_choice == "2":
        # 2. Live Monitoring Mode
        print("\n" + "=" * 80)
        print(f"   BẮT ĐẦU CHẾ ĐỘ THEO DÕI LIÊN TỤC (LIVE MONITOR)")
        print(f"   Mục tiêu số đẹp: '{target_uid_str if target_uid_str else 'Không đặt'}' | Nhấn Ctrl+C để dừng")
        print("=" * 80 + "\n")

        current_known_uid = start_uid
        try:
            while True:
                res = IDTracker.scan_latest_id(conn, start_uid=current_known_uid, max_lookahead_misses=3, verbose=False)
                if res:
                    current_known_uid = res["uid"]
                    now_str = datetime.now().strftime("%H:%M:%S")
                    target_info = ""
                    if target_seq:
                        diff = target_seq - res['seq_num']
                        if diff > 0:
                            target_info = f" | \033[93mCòn {diff} acc -> '{target_uid_str}'\033[0m"
                        elif diff == 0:
                            target_info = f" | \033[92m[ĐÃ ĐẾN MỤC TIÊU SỐ ĐẸP! TẠO ACC NGAY!]\033[0m"
                        else:
                            target_info = f" | \033[91m[Đã qua mục tiêu {abs(diff)} acc]\033[0m"

                    print(f"[{now_str}] \033[92m[ID MỚI NHẤT]\033[0m \033[93m{res['uid']}\033[0m | Tên: '{res['name']}' (Lv {res['level']}){target_info}")

                # Send keep-alive packet (ping / clock) every interval
                time.sleep(3.0)
        except (KeyboardInterrupt, EOFError):
            print("\n\033[93m[ĐÃ DỪNG]\033[0m Đã dừng chế độ theo dõi.")

    conn.close()
