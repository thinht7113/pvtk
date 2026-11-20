"""
PVTK Automated Mail Claim Module (Rút Bạc từ Xác Nhận / Thư Trả Về)
Workflow:
1. Log in to Account & Enter Character (Slot 1 -> Slot 2 -> Slot 3)
2. Query 'Xác nhận/Thư trả về' (Opcode 0x2AFF, mailType = 0xC0) and 'Thư người chơi' (0x20)
3. Extract Bạc / Attachments from all confirmation mails (Opcode 0x2B03)
4. Accurately calculate the exact Silver delta (+X Bạc received) vs Total Wallet Balance
5. Clean/delete claimed mails (Opcode 0x2B05)
6. Report total claimed silver per character and account
"""

import os
import sys
import time
import socket
import threading
from datetime import datetime
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
from core.item_formatter import format_item
from modules.auth import AuthManager, CharacterInfo
from modules.base_runner import CharacterLoopRunner

MAIL_TYPE_TASK = 0x08            # 8 (Thư thưởng nhiệm vụ - Vấn đáp, thành tựu, thưởng hệ thống)
MAIL_TYPE_RETURN_CONFIRM = 0xC0  # 192 (Thư trả về / Xác nhận giao dịch COD)
MAIL_TYPE_PLAYER = 0x20          # 32 (Thư người chơi)

class MailClaimManager:
    @staticmethod
    def get_mail_list(conn: PVTKConnection, mail_type: int = MAIL_TYPE_RETURN_CONFIRM, page_size: int = 10, page_num: int = 1) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Sends Opcode 0x2AFF (CG_MAIL_LIST) to query mail list.
        Returns: (allSize, list of mail dicts)
        """
        for attempt in range(2):
            try:
                while conn.receive_message(timeout=0.05):
                    pass

                msg = Message(0x2AFF)
                msg.putByte(mail_type)
                msg.putByte(page_size)
                msg.putShort(page_num)

                res = conn.send_and_wait(msg, expected_type=0x2AFF, timeout=4.5)
                if not res or res.type != 0x2AFF:
                    if attempt == 0:
                        time.sleep(0.3)
                        continue
                    return 0, []

                all_size = res.getShort()
                if all_size < 0:
                    return 0, []

                page_count = res.getByte()
                mails = []

                for _ in range(page_count):
                    mail_id = res.getLong()
                    to_name = res.getString()
                    status = res.getByte()
                    is_title = res.getBoolean()
                    title = res.getString() if is_title else ""
                    need_check = res.getBoolean()

                    mails.append({
                        "id": mail_id,
                        "to_name": to_name,
                        "status": status,
                        "title": title,
                        "need_check": need_check
                    })

                return all_size, mails

            except Exception:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                return 0, []
        return 0, []

    @staticmethod
    def _read_mail_item(res: Message) -> Optional[Dict[str, Any]]:
        """Reads Item.fromMailBytes structure from 0x2B00 Message."""
        try:
            item_id = res.getShort() & 0xFFFF
            quantity = res.getByte()
            name = res.getString()
            bag_icon = res.getByte()
            grade = res.getByte()
            item_type = res.getByte()
            slot_pos = res.getShort()
            is_upgrade = res.getBoolean()
            return {
                "id": item_id,
                "quantity": quantity,
                "name": name,
                "grade": grade,
                "type": item_type
            }
        except Exception as e:
            print(f"[_read_mail_item ERROR] {e}")
            return None

    @classmethod
    def get_mail_detail(cls, conn: PVTKConnection, mail_id: int) -> Optional[Dict[str, Any]]:
        """
        Sends Opcode 0x2B00 (CG_MAIL_DETAIL) to load mail into session and inspect attachments / fees.
        Parses attached items and selectable choice rewards.
        """
        try:
            while conn.receive_message(timeout=0.05):
                pass
            msg = Message(0x2B00)
            msg.putLong(mail_id)
            res = conn.send_and_wait(msg, expected_type=0x2B00, timeout=3.5)
            if not res or res.type != 0x2B00:
                return None

            to_pid = res.getLong()
            mail_type = res.getByte() if res.hasRemaining() else 0
            is_del = res.getBoolean() if res.hasRemaining() else False
            content = res.getString() if res.hasRemaining() else ""
            datetime = res.getString() if res.hasRemaining() else ""
            money1 = res.getInt() if res.hasRemaining() else 0
            money2 = res.getInt() if res.hasRemaining() else 0
            money3 = res.getInt() if res.hasRemaining() else 0
            req_money1 = res.getInt() if res.hasRemaining() else 0
            req_money2 = res.getInt() if res.hasRemaining() else 0
            req_money3 = res.getInt() if res.hasRemaining() else 0

            # Parse attached items
            attach_items = []
            if res.hasRemaining():
                attach_count = res.getByte()
                for _ in range(attach_count):
                    it = cls._read_mail_item(res)
                    if it:
                        attach_items.append(it)

            # Parse selectable choice items (1 of N rewards)
            select_items = []
            if res.hasRemaining():
                select_count = res.getByte()
                for _ in range(select_count):
                    it = cls._read_mail_item(res)
                    if it:
                        select_items.append(it)

            # Choose 1st reward if selectable
            chosen_select_id = select_items[0]["id"] if select_items else 0
            chosen_select_name = select_items[0]["name"] if select_items else ""

            return {
                "to_pid": to_pid,
                "mail_type": mail_type,
                "content": content,
                "datetime": datetime,
                "silver": money3,
                "req_gold": req_money1,
                "req_silver": req_money3,
                "req_knb": req_money2,
                "attach_items": attach_items,
                "select_items": select_items,
                "select_id": chosen_select_id,
                "select_name": chosen_select_name
            }
        except Exception as e:
            print(f"[get_mail_detail ERROR] {e}")
            return None

    @staticmethod
    def extract_mail_attachment(conn: PVTKConnection, mail_id: int, is_back: bool = False, select_id: int = 0) -> Tuple[bool, int, str]:
        """
        Sends Opcode 0x2B03 (CG_MAIL_EXTRACT_ATTACHMENT) to extract silver and items.
        Automatically handles Receipt Mails (is_back=False) and Return Mails (is_back=True) with select_id for choice rewards.
        Returns: (success: bool, new_total_silver: int, info_or_error: str)
        """
        # Try preferred is_back mode first, then alternate
        modes = [is_back, not is_back]
        for mode in modes:
            try:
                while conn.receive_message(timeout=0.05):
                    pass

                msg = Message(0x2B03)
                msg.putLong(mail_id)
                msg.putBoolean(mode)      # False for Receipt mail, True for Return mail
                msg.putBoolean(True)      # isExtractAll = True
                msg.putInt(select_id)     # selectItemId (0 for all, or selected item ID if choosing 1 of N)

                res = conn.send_and_wait(msg, expected_type=0x2B03, timeout=5.0)
                if not res or res.type != 0x2B03:
                    continue

                code = res.getByte()
                if code < 0:
                    err_msg = res.getString() if res.hasRemaining() else f"Lỗi trích xuất (Mã: {code})"
                    if "loại thao tác này" in err_msg:
                        continue
                    return False, 0, err_msg

                # Success: update money
                money1 = res.getInt()
                money2 = res.getInt()
                money3 = res.getInt()  # Tổng số Bạc trong túi sau khi nhận
                info = ""
                time.sleep(0.2)
                
                # Catch system messages (like "Bạn nhận được Hộp Quà Sự Kiện x1")
                while True:
                    extra_msg = conn.receive_message(timeout=0.5)
                    if not extra_msg:
                        break
                    if extra_msg.type == 0x0F:  # GC_SYSTEM_MESSAGE
                        try:
                            text = extra_msg.getString()
                            if text:
                                if info:
                                    info += ", "
                                info += text
                        except Exception:
                            pass

                return True, money3, info

            except Exception:
                continue
        return False, 0, "Không nhận được phản hồi từ Server"

    @staticmethod
    def delete_mail(conn: PVTKConnection, mail_id: int) -> bool:
        """Sends Opcode 0x2B05 (CG_MAIL_DELETE) to delete mail after claiming."""
        try:
            while conn.receive_message(timeout=0.05):
                pass
            msg = Message(0x2B05)
            msg.putLong(mail_id)
            res = conn.send_and_wait(msg, expected_type=0x2B05, timeout=2.5)
            time.sleep(0.1)
            while conn.receive_message(timeout=0.05):
                pass
            return res is not None
        except Exception:
            return False

    @classmethod
    def claim_all_for_character(cls, conn: PVTKConnection, char_tag: str) -> Dict[str, Any]:
        """
        Extracts attachments from:
        1. 'Thư thưởng nhiệm vụ' (0x08) - Contains Quiz rewards (Vé EXP, quà sự kiện, ...)
        2. 'Xác nhận / Thư trả về' (0xC0) - Contains COD Silver returns & confirmations
        Loops continuously until all pages of both mailboxes are completely processed and cleared.
        """
        result = {
            "mails_claimed": 0,
            "total_silver_gained": 0,
            "latest_silver": 0,
            "rewards_summary": [],
            "items_gained": {},
            "status": "NO_MAILS",
            "message": "Không có thư nào cần nhận"
        }

        # Fetch ALL mail categories (0xFF bitmask)
        target_mail_types = [
            (0xFF, "Tất cả các loại Thư")
        ]

        total_claimed = 0
        total_gained = 0
        last_known_balance = None
        rewards_list = []

        for m_type, m_name in target_mail_types:
            previous_mail_ids = set()
            while True:
                all_size, mails = cls.get_mail_list(conn, mail_type=m_type, page_size=20, page_num=1)
                if not mails:
                    print(f"{char_tag} [HỘP THƯ: {m_name}] \033[90mHộp thư trống hoặc đã nhận hết.\033[0m")
                    break

                print(f"{char_tag} [HỘP THƯ: {m_name}] Tìm thấy \033[93m{len(mails)}\033[0m/{all_size} thư chờ nhận.")
                
                current_mail_ids = set(m["id"] for m in mails)
                if current_mail_ids == previous_mail_ids:
                    print(f"{char_tag} [HỘP THƯ: {m_name}] \033[91m[CẢNH BÁO]\033[0m Phát hiện vòng lặp vô tận (Server không xóa thư). Dừng quét hộp thư này.")
                    break
                previous_mail_ids = current_mail_ids
                
                claimed_in_this_pass = 0

                for m_idx, mail in enumerate(mails):
                    mail_id = mail["id"]
                    sender = mail["to_name"]
                    title = mail.get("title") or m_name

                    # 1. Open mail detail (0x2B00) to inspect fees and load session
                    detail = cls.get_mail_detail(conn, mail_id)
                    
                    # Detect if it's a return/confirm mail based on mail_type parsed from detail, or fallback
                    is_back_mode = False
                    select_item_id = 0
                    
                    if detail:
                        is_back_mode = (detail.get("mail_type", 0) == MAIL_TYPE_RETURN_CONFIRM)
                        
                        # Bỏ qua thư C.O.D yêu cầu thanh toán (Vàng, Bạc, Kim phiếu)
                        if detail.get("req_silver", 0) > 0 or detail.get("req_knb", 0) > 0 or detail.get("req_gold", 0) > 0:
                            fee_str = []
                            if detail.get("req_silver", 0) > 0: fee_str.append(f"{detail['req_silver']:,} Bạc")
                            if detail.get("req_knb", 0) > 0: fee_str.append(f"{detail['req_knb']:,} Kim diệp")
                            if detail.get("req_gold", 0) > 0: fee_str.append(f"{detail['req_gold']:,} Vàng")
                            print(f"{char_tag} [{m_name}] -> [BỎ QUA] Thư #{m_idx + 1} ({title} / {sender}): Yêu cầu phí ({' - '.join(fee_str)}) -> Bỏ qua.")
                            continue
                        select_item_id = detail.get("select_id", 0)

                    # 2. Extract attachments (0x2B03)
                    ok, new_total_silver, info = cls.extract_mail_attachment(conn, mail_id, is_back=is_back_mode, select_id=select_item_id)
                    if ok:
                        total_claimed += 1
                        claimed_in_this_pass += 1
                        
                        gained_this_mail = 0
                        if last_known_balance is not None and new_total_silver > last_known_balance:
                            gained_this_mail = new_total_silver - last_known_balance
                        elif detail and detail.get("silver", 0) > 0:
                            gained_this_mail = detail.get("silver", 0)
                        elif is_back_mode and new_total_silver >= 510001:
                            gained_this_mail = 510001 # Standard COD silver

                        total_gained += gained_this_mail
                        last_known_balance = new_total_silver
                        result["latest_silver"] = new_total_silver

                        # Extract item names with authentic grade colors from detail
                        item_names = []
                        if detail:
                            for it in detail.get("attach_items", []):
                                name = it['name']
                                qty = it.get('quantity', 1)
                                grade = it.get('grade', 1)
                                if name not in result["items_gained"]:
                                    result["items_gained"][name] = {"qty": 0, "grade": grade}
                                result["items_gained"][name]["qty"] += qty
                                item_names.append(format_item(name, grade, qty))
                                
                            if detail.get("select_name"):
                                name = detail['select_name']
                                grade = detail.get('select_grade', 1)
                                if name not in result["items_gained"]:
                                    result["items_gained"][name] = {"qty": 0, "grade": grade}
                                result["items_gained"][name]["qty"] += 1
                                item_names.append(format_item(name, grade, 1))

                        if item_names and gained_this_mail > 0:
                            reward_desc = f"+{gained_this_mail:,} Bạc, {', '.join(item_names)}"
                        elif item_names:
                            reward_desc = ", ".join(item_names)
                        elif gained_this_mail > 0:
                            reward_desc = f"+{gained_this_mail:,} Bạc"
                        else:
                            reward_desc = info if info else "Thư thông báo/Hệ thống (Không có vật phẩm/Bạc)"

                        rewards_list.append(reward_desc)

                        if gained_this_mail > 0 and item_names:
                            print(f"{char_tag} [{m_name}] -> \033[92m[NHẬN THÀNH CÔNG]\033[0m Thư #{m_idx + 1} ('{title}') | \033[93m+{gained_this_mail:,} Bạc\033[0m & {', '.join(item_names)} | Tổng Bạc: \033[92m{new_total_silver:,}\033[0m Bạc")
                        elif gained_this_mail > 0:
                            print(f"{char_tag} [{m_name}] -> \033[92m[NHẬN THÀNH CÔNG]\033[0m Thư #{m_idx + 1} ('{title}') | \033[93mNhận được: +{gained_this_mail:,} Bạc\033[0m | Tổng Bạc: \033[92m{new_total_silver:,}\033[0m Bạc")
                        else:
                            print(f"{char_tag} [{m_name}] -> \033[92m[NHẬN THÀNH CÔNG]\033[0m Thư #{m_idx + 1} ('{title}') | Phần thưởng: {reward_desc} | Tổng Bạc: \033[92m{new_total_silver:,}\033[0m Bạc")

                        # Delete claimed mail to clean mailbox
                        cls.delete_mail(conn, mail_id)
                    else:
                        if "không phải bản thân" in info or "loại thao tác này" in info:
                            print(f"{char_tag} [{m_name}] -> [DỌN DẸP] Thư #{m_idx + 1} ({title} / {sender}): Thư gửi đi/thông báo -> Đã tự động xóa thư.")
                            cls.delete_mail(conn, mail_id)
                            claimed_in_this_pass += 1
                        else:
                            print(f"{char_tag} [{m_name}] -> \033[91m[BỎ QUA/LỖI]\033[0m Thư #{m_idx + 1} ({title} / {sender}): {info}")
                            if any(kw in info.lower() for kw in ["kết nối", "phản hồi", "mạng", "timeout", "socket"]):
                                result["status"] = "NETWORK_ERROR"
                                result["error"] = info
                                result["message"] = info
                                return result

                    time.sleep(0.15)

                if claimed_in_this_pass == 0:
                    # No more claimable mails on this page
                    break

        result["mails_claimed"] = total_claimed
        result["total_silver_gained"] = total_gained
        result["rewards_summary"] = rewards_list

        if total_claimed > 0:
            result["status"] = "SUCCESS"
            
            # Aggregate gained items
            gained_items_str = []
            for name, data in result.get("items_gained", {}).items():
                gained_items_str.append(format_item(name, data["grade"], data["qty"]))
                
            msg = f"Đã nhận {total_claimed} thư (+{total_gained:,} Bạc)"
            if gained_items_str:
                msg += f", {', '.join(gained_items_str)}"
                
            result["message"] = msg
            result["sent_silver"] = total_gained
        else:
            result["status"] = "EMPTY"
            result["message"] = "Không có thư nào cần nhận"

        return result


class MailClaimRunner(CharacterLoopRunner):
    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        self.log(f"{char_tag} \033[92m[VÀO GAME THÀNH CÔNG]\033[0m Vị trí: \033[96m{char.map_name}\033[0m. Đang kiểm tra thư...")

        # Claim Mails
        char_res = MailClaimManager.claim_all_for_character(game_conn, char_tag)
        
        char_res["name"] = char.name
        
        # Lưu kết quả nếu đã nhận được thư thành công (kể cả khi mất mạng ở các thư tiếp theo)
        if char_res.get("mails_claimed", 0) > 0 or char_res.get("total_silver_gained", 0) > 0 or char_res.get("items_gained"):
            account_result["characters_results"].append(char_res)
        
        if char_res.get("status") == "NETWORK_ERROR":
            err_msg = char_res.get("error") or char_res.get("message") or "Mất kết nối với Server khi đang nhận thư"
            account_result["error"] = err_msg
            raise ConnectionError(err_msg)
            
        if char_res.get("mails_claimed", 0) == 0:
            account_result["characters_results"].append(char_res)


def run_all_mail_claim(accounts: List[Tuple], max_workers: int = None, stop_event: Optional[threading.Event] = None):
    total = len(accounts)
    print("\n" + "=" * 90)
    print(f"  BẮT ĐẦU NHẬN THƯ (THƯỞNG NHIỆM VỤ + XÁC NHẬN/TRẢ VỀ) CHO {total} TÀI KHOẢN")
    print("=" * 90)

    start_time = time.time()
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]
    
    try:
        runner = MailClaimRunner(account_tasks, thread_name="Tài khoản", max_retries=4, max_workers=max_workers, stop_event=stop_event)
        all_results = runner.run_all()
    except KeyboardInterrupt:
        print("\n\n\033[91m[BUỘC DỪNG]\033[0m Người dùng nhấn Ctrl+C. Tắt ngay lập tức...")
        import os
        os._exit(0)

    elapsed = round(time.time() - start_time, 1)

    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}
    final_results = [results_by_user.get(acc[0], {"username": acc[0], "error": "Chưa hoàn thành"}) for acc in accounts]

    # Print Summary Table
    import re
    print("\n" + "=" * 125)
    print("               BẢNG TỔNG KẾT NHẬN THƯ (THƯỞNG NHIỆM VỤ & XÁC NHẬN/TRẢ VỀ)")
    print("=" * 125)
    print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<15} | {'SỐ THƯ':<8} | {'PHẦN THƯỞNG & BẠC NHẬN ĐƯỢC':<40} | {'TỔNG BẠC TRONG TÚI':<20} | {'TRẠNG THÁI'}")
    print("-" * 125)

    total_mails_claimed = 0
    total_silver_all = 0
    total_chars_scanned = 0
    all_items_aggregated: Dict[str, int] = {}
    claimed_characters_list: List[Dict[str, Any]] = []

    for acc in accounts:
        u = acc[0]
        acc_res = results_by_user.get(u, {"username": u, "error": "Chưa hoàn thành"})
        if acc_res.get("error"):
            print(f"{u:<15} | {'(Lỗi đăng nhập)':<15} | {'-':<8} | {'-':<22} | {'-':<20} | {acc_res['error']}")
            continue

        for c in acc_res.get("characters_results", []):
            total_chars_scanned += 1
            cname = c.get("name", "")
            claimed = c.get("mails_claimed", 0)
            silver = c.get("latest_silver", 0)
            gained = c.get("total_silver_gained", 0)
            total_mails_claimed += claimed
            total_silver_all += gained

            items_dict = c.get("items_gained", {})
            for iname, idata in items_dict.items():
                qty = idata.get("qty", 1) if isinstance(idata, dict) else 1
                all_items_aggregated[iname] = all_items_aggregated.get(iname, 0) + qty

            if claimed > 0:
                char_items = {}
                for iname, idata in items_dict.items():
                    qty = idata.get("qty", 1) if isinstance(idata, dict) else 1
                    char_items[iname] = char_items.get(iname, 0) + qty

                claimed_characters_list.append({
                    "username": u,
                    "char_name": cname,
                    "mails": claimed,
                    "silver": gained,
                    "latest_silver": silver,
                    "items": char_items
                })

                status_str = f"\033[92mTHÀNH CÔNG\033[0m"
                silver_str = f"{silver:,} Bạc" if silver > 0 else "-"
                
                gained_parts = []
                if gained > 0:
                    gained_parts.append(f"\033[93m+{gained:,} Bạc\033[0m")
                for iname, idata in items_dict.items():
                    gained_parts.append(format_item(iname, idata["grade"], idata["qty"]))
                
                if gained_parts:
                    raw_str = ", ".join(gained_parts)
                    clean_len = len(re.sub(r'\033\[[0-9;]*m', '', raw_str))
                    pad = max(0, 40 - clean_len)
                    gained_str = raw_str + (" " * pad)
                else:
                    gained_str = f"{'Thư hệ thống/Không có VP':<40}"
            else:
                status_str = "Không có thư"
                silver_str = "-"
                gained_str = f"{'-':<40}"

            print(f"{u:<15} | {cname:<15} | {claimed:<8} | {gained_str} | {silver_str:<20} | {status_str}")

    print("-" * 125)
    print(f"-> Tổng số thư đã nhận: \033[92m{total_mails_claimed}\033[0m thư | Tổng Bạc thực nhận: \033[93m+{total_silver_all:,}\033[0m Bạc")
    print(f"-> Tổng thời gian hoàn thành: {elapsed} giây")
    print("=" * 125)

    failed_accounts = []
    for acc in accounts:
        u = acc[0]
        acc_res = results_by_user.get(u, {})
        err = acc_res.get("error", "")
        if err or not acc_res.get("completed", False):
            failed_accounts.append({
                "username": u,
                "error": err or "Chưa hoàn thành nhận thư"
            })

    return {
        "total_accounts": len(accounts),
        "total_chars_scanned": total_chars_scanned,
        "total_mails": total_mails_claimed,
        "total_silver": total_silver_all,
        "items_summary": all_items_aggregated,
        "claimed_characters": claimed_characters_list,
        "failed_accounts": failed_accounts,
        "elapsed": elapsed,
        "cancelled": bool(stop_event and stop_event.is_set())
    }
