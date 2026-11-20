"""
PVTK Automated Mail Sender Module (Hệ thống Gửi Thư Giao Dịch COD)
Allows all characters across all accounts to automatically send COD mail to a designated player target:
- 'Nhận' (COD payment required from receiver): Configurable (Default: 515,000 Bạc)
- 'Tặng' (Silver gift attached): Configurable (Default: 1 Bạc to prevent empty mail errors)
- 'Người nhận': Supports Player ID (e.g. 'u33027042', 'am6278190', '1033027042') or Character Name.
- Auto-Sell: If Bạc == 0, automatically scans bag and sells 'Dược Thảo Hoạt Lực' (Opcode 0x2EE6) to obtain Silver.
- If no Silver and no item to sell -> Skips and reports in output summary.
"""

import sys
import time
from typing import List, Tuple, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto
from modules.auth import AuthManager, CharacterInfo
from modules.base_runner import CharacterLoopRunner


def parse_player_id(target_str: str) -> Optional[int]:
    """
    Implements com.hz.core.Define.getIdByAreaID.
    Converts Area-ID string (e.g. 'u33027042', 'am6278190', '1033027042') to 32-bit int PlayerID.
    Returns integer PlayerID if valid ID format, else None (indicating Character Name).
    """
    s = str(target_str).strip()
    if not s:
        return None

    # Pure numeric digits
    if s.isdigit():
        return int(s)

    c1 = s[0].lower()
    c2 = s[1].lower() if len(s) >= 2 else '!'

    # Area ID prefix format (e.g., 'u33027042', 'am6278190')
    if 'a' <= c1 <= 'z':
        if 'a' <= c2 <= 'z' and len(s) > 2 and s[2:].isdigit():
            value = (ord(c2) - ord('a')) + 26
            num_part = s[2:]
            return (value * 50000000) + int(num_part)
        elif len(s) > 1 and s[1:].isdigit():
            value = ord(c1) - ord('a')
            num_part = s[1:]
            return (value * 50000000) + int(num_part)

    return None


import threading

PRINT_LOCK = threading.Lock()

def safe_print(*args, **kwargs):
    with PRINT_LOCK:
        print(*args, **kwargs, flush=True)


class MailManager:
    @staticmethod
    def auto_sell_herb(conn: PVTKConnection) -> bool:
        """
        Sends Opcode 0x2EE3 (Bag Clean/Refresh) to retrieve bag inventory,
        finds 'Dược Thảo Hoạt Lực' (or sellable herbs), extracts real (slotPos, item_id),
        and sends Opcode 0x2EE6 with shopID=3 (VIP Direct Sell) to sell 1 herb for Silver.
        Returns True if an item was sold, False otherwise.
        """
        try:
            while conn.receive_message(timeout=0.05):
                pass

            # 1. Query bag content
            req = Message(0x2EE3)
            req.putInt(0x10)  # Block flag for bag/items
            res = conn.send_and_wait(req, expected_type=0x2EE3, timeout=3.5)
            if not res or res.type != 0x2EE3:
                return False

            raw_data = bytes(res.data)
            if len(raw_data) < 20:
                return False

            code = raw_data[0]
            if code != 0:
                return False

            # Search for 'Dược Thảo Hoạt Lực' or 'Dược Thảo' UTF-16BE encoded in buffer
            target_str = "Dược Thảo"
            target_bytes = target_str.encode("utf-16-be")
            pos = raw_data.find(target_bytes)

            if pos != -1:
                str_len_pos = pos - 3
                item_id_pos = str_len_pos - 2
                item_type_pos = item_id_pos - 1
                status_pos = item_type_pos - 1
                slot_pos_pos = status_pos - 2

                import struct
                slotPos = struct.unpack(">h", raw_data[slot_pos_pos:slot_pos_pos+2])[0]
                item_id = struct.unpack(">H", raw_data[item_id_pos:item_id_pos+2])[0]

                # Sell 1 item
                sell_msg = Message(0x2EE6)
                sell_msg.putShort(3)       # shopID = 3 (VIP Direct Sell)
                sell_msg.putInt(item_id)   # real itemID
                sell_msg.putShort(slotPos) # real slotPos
                sell_msg.putByte(1)        # count = 1

                sell_res = conn.send_and_wait(sell_msg, expected_type=0x2EE6, timeout=3.0)
                if sell_res and sell_res.type == 0x2EE6:
                    sell_code = sell_res.getByte()
                    if sell_code >= 0:
                        time.sleep(0.2)
                        while conn.receive_message(timeout=0.05):
                            pass
                        return True

            return False
        except Exception:
            return False

    @staticmethod
    def send_cod_mail(
        conn: PVTKConnection,
        receiver_target: str,
        receive_silver: int = 515000,
        send_silver: int = 1,
        content: str = "PVTK"
    ) -> Tuple[bool, str]:
        """
        Sends Opcode 0x2B01 (CG_MAIL_SEND) with retry.
        Supports Player ID (type 1) or Character Name (type 2).
        Returns (success: bool, message: str)
        """
        player_id = parse_player_id(receiver_target)

        for attempt in range(2):
            try:
                target_str = str(receiver_target).strip()
                if not target_str:
                    return False, "Chưa thiết lập người nhận trong .env (MAIL_RECEIVER_TARGET)"

                while conn.receive_message(timeout=0.05):
                    pass

                msg = Message(0x2B01)
                msg.putString(content)
                msg.putInt(0)               # money1 (Vàng tặng)
                msg.putInt(0)               # money2 (Kim diệp tặng)
                msg.putInt(send_silver)     # money3 (Bạc tặng)
                msg.putInt(0)               # reqMoney1 (Vàng nhận)
                msg.putInt(0)               # reqMoney2 (Kim diệp nhận)
                msg.putInt(receive_silver)  # reqMoney3 (Bạc nhận)
                msg.putByte(0)              # itemCount

                if player_id is not None:
                    msg.putByte(1)          # 1 = Gửi theo Player ID
                    msg.putInt(player_id)
                else:
                    msg.putByte(2)          # 2 = Gửi theo Tên nhân vật
                    msg.putString(target_str)

                res = conn.send_and_wait(msg, expected_type=0x2B01, timeout=4.5)
                if not res:
                    if attempt == 0:
                        time.sleep(0.4)
                        continue
                    return False, "Không nhận được phản hồi kết quả gửi thư từ Server"

                code = res.getByte()
                if code < 0:
                    err_msg = res.getString() if res.hasRemaining() else f"Lỗi gửi thư (Mã: {code})"
                    return False, err_msg

                time.sleep(0.15)
                while conn.receive_message(timeout=0.05):
                    pass

                return True, "Gửi thư COD thành công"

            except Exception as e:
                if attempt == 0:
                    time.sleep(0.4)
                    continue
                return False, f"Ngoại lệ gửi thư: {e}"

        return False, "Gửi thư thất bại sau 2 lần thử"


class MailSenderRunner(CharacterLoopRunner):
    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        receiver_target = task[5]
        receive_silver = task[6]
        send_silver = task[7]

        target_pid = parse_player_id(receiver_target)

        is_self = False
        if target_pid is not None and char.id == target_pid:
            is_self = True
        elif str(char.name).strip().lower() == receiver_target.strip().lower():
            is_self = True

        if is_self:
            self.log(f"{char_tag} [BỎ QUA] Đây chính là nhân vật người nhận thư.")
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "status": "SKIP_SELF",
                "message": "Trùng người nhận"
            })
            return

        ok, msg_res = MailManager.send_cod_mail(
            game_conn,
            receiver_target=receiver_target,
            receive_silver=receive_silver,
            send_silver=send_silver,
            content=settings.mail_content
        )

        sold_herb = False
        if not ok and any(w in msg_res.lower() for w in ["tiền không đủ", "không đủ", "bạc", "money", "phí"]):
            self.log(f"{char_tag} [CẢNH BÁO] Chưa đủ Bạc ({msg_res}). Đang tự động quét bán 'Dược Thảo Hoạt Lực' trong túi đồ...")
            sold_herb = MailManager.auto_sell_herb(game_conn)
            if sold_herb:
                self.log(f"{char_tag} \033[92m[BÁN THÀNH CÔNG]\033[0m Đã bán 1 Dược Thảo Hoạt Lực lấy Bạc. Đang gửi lại thư...")
                time.sleep(0.3)
                ok, msg_res = MailManager.send_cod_mail(
                    game_conn,
                    receiver_target=receiver_target,
                    receive_silver=receive_silver,
                    send_silver=send_silver,
                    content=settings.mail_content
                )
            else:
                self.log(f"{char_tag} \033[91m[BỎ QUA]\033[0m Không có Bạc và không có Dược Thảo Hoạt Lực để bán.")
                account_result["characters_results"].append({
                    "name": char.name,
                    "id": char.id,
                    "status": "SKIP_NO_SILVER",
                    "sold_herb": False,
                    "message": "Không có bạc và không có Dược Thảo Hoạt Lực để bán"
                })
                return

        status_str = "SUCCESS" if ok else "FAILED"
        if ok:
            extra_log = " (Đã tự bán Dược Thảo)" if sold_herb else ""
            self.log(f"{char_tag} \033[92m[GỬI THƯ THÀNH CÔNG{extra_log}]\033[0m Yêu cầu nhận: {receive_silver:,} Bạc | Tặng: {send_silver} Bạc -> Đến ID: '{receiver_target}'")
        else:
            self.log(f"{char_tag} \033[91m[GỬI THƯ THẤT BẠI]\033[0m {msg_res}")

        account_result["characters_results"].append({
            "name": char.name,
            "id": char.id,
            "status": status_str,
            "sold_herb": sold_herb,
            "message": msg_res
        })


def run_all_mail_sender(accounts: List[Tuple], receiver_name_or_id: str = None, max_workers: int = None):
    receiver = receiver_name_or_id
    if not receiver:
        try:
            print("\n" + "=" * 80)
            print("                 CẤU HÌNH GỬI THƯ GIAO DỊCH (COD MAIL)")
            print("=" * 80)
            print(f"-> Mặc định Bạc yêu cầu (COD): \033[92m{settings.mail_receive_silver:,}\033[0m Bạc | Bạc gửi kèm: \033[96m{settings.mail_send_silver}\033[0m")
            input_target = input("-> Người nhận (Tên / ID): ").strip()
            if not input_target:
                print("[HỦY THAO TÁC] Đã quay lại Menu chính.")
                return
            receiver = input_target
        except (KeyboardInterrupt, EOFError):
            print("\n[HỦY THAO TÁC] Đã quay lại Menu chính.")
            return

    total = len(accounts)
    rec_silver = settings.mail_receive_silver
    snd_silver = settings.mail_send_silver

    print("\n" + "=" * 80)
    print(f"  BẮT ĐẦU GỬI THƯ GIAO DỊCH BẠC (COD) CHO {total} TÀI KHOẢN")
    print(f"  -> Người nhận (ID): \033[93m{receiver}\033[0m | Bạc nhận (COD): \033[92m{rec_silver:,}\033[0m | Bạc tặng: \033[96m{snd_silver}\033[0m")
    print("=" * 80)

    start_time = time.time()
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None, receiver, rec_silver, snd_silver)
        for i, acc in enumerate(accounts)
    ]
    
    try:
        runner = MailSenderRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers)
        all_results = runner.run_all()
    except KeyboardInterrupt:
        print("\n\n\033[91m[BUỘC DỪNG]\033[0m Người dùng nhấn Ctrl+C. Tắt ngay lập tức...")
        import os
        os._exit(0)

    elapsed = round(time.time() - start_time, 1)
    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}


    final_results = [results_by_user.get(acc[0], {"username": acc[0], "error": "Chưa hoàn thành"}) for acc in accounts]

    # Print Summary Table
    print("\n" + "=" * 90)
    print("                    BẢNG TỔNG KẾT GỬI THƯ GIAO DỊCH (COD MAIL)")
    print("=" * 90)
    print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<15} | {'NGƯỜI NHẬN (ID)':<18} | {'BẠC NHẬN (COD)':<15} | {'TRẠNG THÁI'}")
    print("-" * 90)

    success_count = 0
    failed_count = 0
    skip_count = 0

    for acc in final_results:
        u = acc["username"]
        if acc.get("error"):
            print(f"{u:<15} | {'(Lỗi đăng nhập)':<15} | {receiver:<18} | {'-':<15} | {acc['error']}")
            continue

        for c in acc.get("characters_results", []):
            cname = c.get("name", "")
            st = c.get("status", "")
            msg = c.get("message", "")

            if st == "SUCCESS":
                success_count += 1
                if c.get("sold_herb"):
                    status_str = "\033[92mTHÀNH CÔNG (Đã tự bán Dược Thảo)\033[0m"
                else:
                    status_str = "\033[92mTHÀNH CÔNG\033[0m"
            elif st == "SKIP_SELF":
                skip_count += 1
                status_str = "\033[93mBỎ QUA (Trùng người nhận)\033[0m"
            elif st == "SKIP_NO_SILVER":
                skip_count += 1
                status_str = "\033[91mBỎ QUA (Không có Bạc & Dược Thảo)\033[0m"
            else:
                failed_count += 1
                status_str = f"\033[91mTHẤT BẠI ({msg})\033[0m"

            print(f"{u:<15} | {cname:<15} | {receiver:<18} | {rec_silver:<15,} | {status_str}")

    print("-" * 90)
    print(f"-> Gửi thành công: {success_count} thư | Thất bại: {failed_count} | Bỏ qua: {skip_count}")
    print(f"-> Tổng thời gian hoàn thành: {elapsed} giây")
    print("=" * 90)
