import time
import threading
from typing import List, Tuple, Dict, Any, Optional

from core.network import PVTKConnection
from core.message import Message
from modules.exp_ticket_sender import ExpTicketSender
from modules.base_runner import CharacterLoopRunner

print_lock = threading.Lock()

def safe_print(msg: str):
    with print_lock:
        print(msg)


class BagCleanerManager:
    @staticmethod
    def use_bag_item(conn: PVTKConnection, slot_pos: int, quantity: int) -> int:
        if quantity <= 0:
            return 0
        used_count = 0
        try:
            # Clear buffer
            while conn.receive_message(timeout=0.01):
                pass

            for _ in range(quantity):
                msg = Message(0x2EE1)
                msg.putByte(0) # wait_type
                msg.putByte(3) # action_type = Use
                msg.putShort(slot_pos)
                msg.putBoolean(False) # is_upgrade
                conn.send_message(msg)
                used_count += 1
                time.sleep(0.04) # 40ms delay between uses
            
            time.sleep(0.1) # short delay after burst
        except Exception:
            pass
        return used_count

    @staticmethod
    def sell_bag_item(conn: PVTKConnection, slot_pos: int, item_id: int, quantity: int) -> bool:
        try:
            while conn.receive_message(timeout=0.01):
                pass
                
            msg = Message(0x2F05)
            msg.putByte(1) # Number of items to sell
            msg.putInt(item_id)
            msg.putShort(slot_pos)
            msg.putShort(quantity)
            
            res = conn.send_and_wait(msg, expected_type=0x2F05, timeout=2.5)
            if res and res.type == 0x2F05:
                code = res.getByte()
                return code >= 0
            return False
        except Exception:
            return False


class BagCleanerRunner(CharacterLoopRunner):
    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        # In game, load bag items
        bag_items = ExpTicketSender.get_bag_items(game_conn)
        
        used_exp_count = 0
        
        if bag_items:
            for item in bag_items:
                name_lower = item.name.lower()
                
                # Vé EXP Nhân Vật (Khóa) -> Dùng (Chỉ dùng vé trạng thái khóa)
                if ("vé exp nhân vật" in name_lower or "ve exp nhan vat" in name_lower) and item.quantity > 0:
                    if item.is_locked() or "khóa" in name_lower or "khoa" in name_lower:
                        self.log(f"{char_tag} \033[96m[TÌM THẤY VÉ EXP KHÓA]\033[0m Đang dùng {item.quantity} vé ở ô {item.slotPos}...")
                        used = BagCleanerManager.use_bag_item(game_conn, item.slotPos, item.quantity)
                        used_exp_count += used
                        if used > 0:
                            self.log(f"{char_tag} \033[92m[DÙNG THÀNH CÔNG]\033[0m Đã dùng {used} vé EXP!")
        
        if used_exp_count == 0:
            self.log(f"{char_tag} \033[90m[TRỐNG]\033[0m Không có Vé EXP nhân vật khóa.")
            msg = "Không có vé EXP khóa"
            status_str = "EMPTY"
        else:
            msg = f"Đã dùng {used_exp_count} Vé EXP khóa"
            status_str = "SUCCESS"
            
        account_result["characters_results"].append({
            "name": char.name,
            "id": char.id,
            "used_exp": used_exp_count,
            "status": status_str,
            "message": msg
        })


def run_all_exp_nv(accounts: List[Tuple], max_workers: int = None) -> Dict[str, Any]:
    total_accs = len(accounts)
    
    print("\n" + "=" * 95)
    print(f"  BẮT ĐẦU SỬ DỤNG VÉ EXP NHÂN VẬT (KHÓA) CHO {total_accs} TÀI KHOẢN")
    print(f"  ĐA LUỒNG: {total_accs}")
    print("=" * 95 + "\n")

    start_time = time.time()
    
    account_tasks = [
        (i + 1, total_accs, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]
    
    runner = BagCleanerRunner(account_tasks, thread_name="Tài khoản", max_workers=max_workers, max_retries=2)
    all_results = runner.run_all()
    
    results_by_user = {}
    for res in all_results:
        if "username" in res:
            results_by_user[res["username"]] = res

    total_time = round(time.time() - start_time, 1)
    total_exp_used = 0
    total_chars_scanned = 0
    used_characters_list = []

    # Summary Table
    print("\n" + "=" * 95)
    print("               BẢNG TỔNG KẾT SỬ DỤNG VÉ EXP NHÂN VẬT (KHÓA)")
    print("=" * 95)
    print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<18} | {'VÉ ĐÃ DÙNG':<12} | {'TRẠNG THÁI':<20} | {'GHI CHÚ'}")
    print("-" * 95)

    for acc in accounts:
        username = acc[0]
        acc_res = results_by_user.get(username, {"username": username, "error": "Chưa hoàn thành"})
        if acc_res.get("error"):
            print(f"{username:<15} | {'-':<18} | {'0':<12} | \033[91m{'LỖI':<20}\033[0m | {acc_res['error']}")
            continue

        chars_res = acc_res.get("characters_results", [])
        if not chars_res:
            print(f"{username:<15} | {'-':<18} | {'0':<12} | \033[90m{'KHÔNG CÓ NV':<20}\033[0m | -")
            continue

        for cr in chars_res:
            total_chars_scanned += 1
            cname = cr.get("name", "-")
            used_tk = cr.get("used_exp", 0)
            status = cr.get("status", "-")
            msg = cr.get("message", "-")

            total_exp_used += used_tk

            if used_tk > 0:
                used_characters_list.append({
                    "username": username,
                    "char_name": cname,
                    "used_exp": used_tk
                })

            if status == "SUCCESS":
                status_colored = f"\033[92m{'THÀNH CÔNG':<20}\033[0m"
            elif status == "EMPTY":
                status_colored = f"\033[90m{'TRỐNG':<20}\033[0m"
            else:
                status_colored = f"\033[91m{'THẤT BẠI':<20}\033[0m"

            print(f"{username:<15} | {cname:<18} | {used_tk:<12} | {status_colored} | {msg}")

    print("-" * 95)
    print(f"-> TỔNG KẾT: \033[96mĐã dùng {total_exp_used:,} Vé EXP Nhân Vật (Khóa)\033[0m trên {len(used_characters_list)} nhân vật")
    print(f"-> Tổng thời gian hoàn thành: {total_time} giây")
    print("=" * 95 + "\n")

    return {
        "total_accounts": total_accs,
        "total_chars_scanned": total_chars_scanned,
        "total_exp_used": total_exp_used,
        "used_characters": used_characters_list,
        "elapsed": total_time
    }

# Backward compatibility alias
run_all_bag_cleaner = run_all_exp_nv
