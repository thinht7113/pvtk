"""
PVTK Item Deleter Module
Xóa toàn bộ vật phẩm chỉ định theo từ khóa trong túi đồ của tất cả nhân vật.
Hỗ trợ nhiều từ khóa cách nhau bởi dấu phẩy (',').
Tự động chuẩn hóa tiếng Việt (có dấu hoặc không dấu đều khớp).
Bảo vệ tuyệt đối trang bị đang mặc trên người (slot < 30).
"""

import time
import threading
import unicodedata
from typing import List, Tuple, Dict, Any, Optional

from core.network import PVTKConnection
from core.message import Message
from core.config import settings
from modules.exp_ticket_sender import ExpTicketSender, Item
from modules.base_runner import CharacterLoopRunner


def remove_accents(input_str: str) -> str:
    """Loại bỏ dấu tiếng Việt để so sánh tìm kiếm linh hoạt."""
    if not input_str:
        return ""
    nfkd = unicodedata.normalize('NFKD', input_str)
    no_d = nfkd.replace('đ', 'd').replace('Đ', 'D')
    return "".join([c for c in no_d if not unicodedata.combining(c)]).lower().strip()


def matches_keywords(item_name: str, keywords: List[str]) -> bool:
    """
    Kiểm tra tên vật phẩm có chứa bất kỳ từ khóa nào trong danh sách keywords không.
    So sánh không phân biệt hoa thường và hỗ trợ cả tiếng Việt có dấu / không dấu.
    """
    if not item_name or not keywords:
        return False
    name_lower = item_name.lower().strip()
    name_no_acc = remove_accents(name_lower)
    
    for kw in keywords:
        kw_clean = kw.lower().strip()
        if not kw_clean:
            continue
        kw_no_acc = remove_accents(kw_clean)
        if kw_clean in name_lower or kw_no_acc in name_no_acc:
            return True
    return False


class ItemDeleterManager:
    @staticmethod
    def sell_bag_item(conn: PVTKConnection, slot_pos: int, item_id: int, quantity: int = 1) -> Tuple[bool, int, int]:
        """
        Bán vật phẩm trong túi đồ vào Shop/NPC lấy Bạc (tương ứng nút [Bán] trên giao diện game).
        Gửi gói tin CG_AUTO_SELL (Opcode 0x2F05) hoặc CG_ITEMSHOP_SELL (Opcode 0x2EE6).
        Thu về Bạc trực tiếp cho nhân vật.
        Trả về: (thành công: bool, bạc nhận được: int, tổng bạc mới: int)
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass

            qty = quantity if quantity > 0 else 1

            # 1. Gói tin bán từ túi đồ chuẩn của game (Opcode 0x2F05: CG_AUTO_SELL)
            # Tương đương nút [Bán] trên popup chi tiết món đồ
            msg_sell = Message(0x2F05)
            msg_sell.putByte(1)        # Số lượng loại vật phẩm gửi bán = 1
            msg_sell.putInt(item_id)   # item id
            msg_sell.putShort(slot_pos)# vị trí slot trong túi đồ
            msg_sell.putShort(qty)     # số lượng món
            res_sell = conn.send_and_wait(msg_sell, expected_type=0x2F05, timeout=2.5)
            if res_sell and res_sell.type == 0x2F05:
                code_sell = res_sell.getByte()
                if code_sell >= 0:
                    tempsize = code_sell
                    for _ in range(tempsize):
                        if res_sell.offset + 8 <= len(res_sell.data):
                            res_sell.getInt()   # itemid
                            res_sell.getShort() # itemcount
                            res_sell.getShort() # itempos
                    money1 = res_sell.getInt() if res_sell.offset + 4 <= len(res_sell.data) else 0 # gold
                    money2 = res_sell.getInt() if res_sell.offset + 4 <= len(res_sell.data) else 0 # leaf
                    money3 = res_sell.getInt() if res_sell.offset + 4 <= len(res_sell.data) else 0 # silver total
                    getmoney = res_sell.getInt() if res_sell.offset + 4 <= len(res_sell.data) else 0 # silver gained
                    return True, getmoney, money3

            # 2. Gói tin bán vào Shop NPC VIP (Opcode 0x2EE6: CG_ITEMSHOP_SELL)
            msg_shop = Message(0x2EE6)
            msg_shop.putShort(3)       # shopID = 3 (VIP Direct Sell)
            msg_shop.putInt(item_id)   # item id
            msg_shop.putShort(slot_pos)# vị trí slot trong túi
            msg_shop.putByte(min(qty, 255))
            res_shop = conn.send_and_wait(msg_shop, expected_type=0x2EE6, timeout=2.5)
            if res_shop and res_shop.type == 0x2EE6:
                code_shop = res_shop.getByte()
                if code_shop >= 0:
                    money3 = res_shop.getInt() if res_shop.offset + 4 <= len(res_shop.data) else 0
                    return True, 0, money3

            return False, 0, 0
        except Exception:
            return False, 0, 0

    # Alias để tương thích ngược các code cũ gọi delete_bag_item
    delete_bag_item = sell_bag_item

    @staticmethod
    def clean_bag(conn: PVTKConnection):
        """Sắp xếp lại túi đồ sau khi xóa (Opcode 0x2EE3)."""
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(0x2EE3)
            msg.putInt(0x10) # Block 16: Items
            conn.send_message(msg)
            time.sleep(0.2)
        except Exception:
            pass


class ItemDeleterRunner(CharacterLoopRunner):
    def __init__(
        self,
        accounts: List[Tuple],
        keywords: List[str],
        thread_name: str = "Tài khoản",
        max_retries: int = 10,
        max_workers: Optional[int] = None
    ):
        super().__init__(accounts=accounts, thread_name=thread_name, max_retries=max_retries, max_workers=max_workers)
        self.keywords = keywords

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        # Đọc dữ liệu túi đồ hiện tại
        bag_items = ExpTicketSender.get_bag_items(game_conn)
        
        # Dự phòng: Nếu get_bag_items trả về rỗng, dùng bag_parser quét raw packet
        if not bag_items:
            try:
                from modules.bag_parser import parse_all_items_in_bag
                msg = Message(0x2EE3)
                msg.putInt(0x10)
                res = game_conn.send_and_wait(msg, expected_type=0x2EE3, timeout=3.0)
                if res and res.data:
                    parsed_dict = parse_all_items_in_bag(bytes(res.data))
                    for slot, info in parsed_dict.items():
                        it = Item()
                        it.slotPos = slot
                        it.id = info.get("item_id", 0)
                        it.name = info.get("item_name", "")
                        it.quantity = 1
                        bag_items.append(it)
            except Exception:
                pass

        sold_slots = 0
        sold_total_qty = 0
        sold_names = []
        silver_gained = 0

        # Lọc các vật phẩm trong túi (slotPos >= 30, không chạm vào đồ đang mặc slot 0-29)
        target_items = [it for it in bag_items if it.slotPos >= 30 and matches_keywords(it.name, self.keywords)]

        if not target_items:
            self.log(f"{char_tag} \033[90m[TRỐNG]\033[0m Không có vật phẩm nào khớp từ khóa: {self.keywords}")
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "deleted_slots": 0,
                "deleted_qty": 0,
                "sold_slots": 0,
                "sold_qty": 0,
                "silver_gained": 0,
                "details": "Không có",
                "status": "TRỐNG"
            })
            return

        self.log(f"{char_tag} 🔍 \033[93m[TÌM THẤY {len(target_items)} Ô VẬT PHẨM MỤC TIÊU]\033[0m Đang tiến hành bán vào Shop...")

        sold_items_map = {}
        sold_items_silver = {}
        sold_items_slots = {}

        for it in target_items:
            qty = it.quantity if it.quantity > 0 else 1
            success, gained, total_s = ItemDeleterManager.sell_bag_item(game_conn, it.slotPos, it.id, qty)
            if success:
                # Nếu getmoney từ gói tin = 0 nhưng vật phẩm là Kim Cương Khóa(Đổi tiền Bạc) (30k/món), tính giá chuẩn
                if gained <= 0 and ("kim cương" in it.name.lower() or "đổi tiền bạc" in it.name.lower() or "doi tien bac" in it.name.lower()):
                    gained = qty * 30000

                sold_slots += 1
                sold_total_qty += qty
                silver_gained += gained
                sold_names.append(f"{qty}x {it.name}")
                sold_items_map[it.name] = sold_items_map.get(it.name, 0) + qty
                sold_items_silver[it.name] = sold_items_silver.get(it.name, 0) + gained
                sold_items_slots[it.name] = sold_items_slots.get(it.name, 0) + 1
                gain_str = f" (+{gained:,} Bạc)" if gained > 0 else ""
                self.log(f"{char_tag} 💰 \033[92m[ĐÃ BÁN THÀNH CÔNG]\033[0m Ô #{it.slotPos}: {qty} x \033[96m{it.name}\033[0m{gain_str}")
            else:
                self.log(f"{char_tag} ⚠️ \033[91m[BÁN THẤT BẠI]\033[0m Ô #{it.slotPos}: {qty} x {it.name} (Vật phẩm không thể bán vào Shop)")
            time.sleep(0.1)

        # Thu dọn túi đồ cho gọn gàng
        if sold_slots > 0:
            ItemDeleterManager.clean_bag(game_conn)

        details_str = ", ".join(sold_names[:3])
        if len(sold_names) > 3:
            details_str += f" (+{len(sold_names) - 3} món khác)"
        if silver_gained > 0:
            details_str += f" (+{silver_gained:,} Bạc)"

        account_result["characters_results"].append({
            "name": char.name,
            "id": char.id,
            "deleted_slots": sold_slots,
            "deleted_qty": sold_total_qty,
            "sold_slots": sold_slots,
            "sold_qty": sold_total_qty,
            "silver_gained": silver_gained,
            "deleted_items_map": sold_items_map,
            "sold_items_map": sold_items_map,
            "sold_items_silver": sold_items_silver,
            "sold_items_slots": sold_items_slots,
            "deleted_names": sold_names,
            "sold_names": sold_names,
            "details": details_str if details_str else "Thất bại",
            "status": "ĐÃ BÁN" if sold_slots > 0 else "THẤT BẠI"
        })


def run_all_item_deleter(accounts: List[Tuple], keywords: List[str], max_workers: int = None):
    total_accs = len(accounts)
    kw_display = ", ".join([f"'{k}'" for k in keywords])
    
    print("\n" + "=" * 90)
    print(f"   💰 BẮT ĐẦU BÁN VẬT PHẨM CHỈ ĐỊNH CHO {total_accs} TÀI KHOẢN VÀO SHOP")
    print(f"-> Từ khóa bán: \033[93m[{kw_display}]\033[0m")
    print(f"-> Luồng đồng thời: \033[92m{min(total_accs, max_workers) if max_workers else total_accs}\033[0m")
    print("=" * 90 + "\n", flush=True)

    start_time = time.time()
    
    account_tasks = [
        (i + 1, total_accs, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]
    
    runner = ItemDeleterRunner(
        account_tasks,
        keywords=keywords,
        thread_name="Tài khoản",
        max_retries=10,
        max_workers=max_workers
    )
    all_results = runner.run_all()
    
    results_by_user = {}
    for res in all_results:
        if res and "username" in res:
            results_by_user[res["username"]] = res

    total_time = time.time() - start_time
    total_slots_sold = 0
    total_qty_sold = 0
    total_silver_gained = 0

    # Summary Table
    print("\n" + "=" * 110)
    print("                   BẢNG TỔNG KẾT TIẾN TRÌNH BÁN VẬT PHẨM VÀO SHOP")
    print("=" * 110)
    print(f"{'STT':<4} | {'TÀI KHOẢN':<16} | {'TÊN NHÂN VẬT':<18} | {'SỐ Ô BÁN':<10} | {'SỐ LƯỢNG':<10} | {'BẠC THU VỀ':<14} | {'TRẠNG THÁI':<12}")
    print("-" * 110)

    stt = 1
    for acc in accounts:
        username = acc[0]
        res = results_by_user.get(username)

        if not res or res.get("error"):
            err_msg = res.get("error", "Lỗi mạng / Không vào được") if res else "Chưa hoàn tất"
            print(f"{stt:<4} | {username:<16} | {'---':<18} | {'0':<10} | {'0':<10} | {'0':<14} | \033[91m{'LỖI':<12}\033[0m")
            stt += 1
            continue

        char_res = res.get("characters_results", [])
        if not char_res:
            print(f"{stt:<4} | {username:<16} | {'Không có NV':<18} | {'0':<10} | {'0':<10} | {'0':<14} | \033[90m{'TRỐNG':<12}\033[0m")
            stt += 1
            continue

        for cr in char_res:
            c_name = cr.get("name", "Unknown")
            c_slots = cr.get("sold_slots", cr.get("deleted_slots", 0))
            c_qty = cr.get("sold_qty", cr.get("deleted_qty", 0))
            c_silver = cr.get("silver_gained", 0)
            c_status = cr.get("status", "TRỐNG")

            total_slots_sold += c_slots
            total_qty_sold += c_qty
            total_silver_gained += c_silver

            if c_status in ("ĐÃ BÁN", "ĐÃ XÓA"):
                status_color = f"\033[92m{c_status:<12}\033[0m"
            elif c_status == "TRỐNG":
                status_color = f"\033[90m{c_status:<12}\033[0m"
            else:
                status_color = f"\033[91m{c_status:<12}\033[0m"

            silver_display = f"+{c_silver:,}" if c_silver > 0 else "0"
            print(f"{stt:<4} | {username:<16} | {c_name:<18} | {c_slots:<10} | {c_qty:<10} | {silver_display:<14} | {status_color}")
            stt += 1

    print("=" * 110)
    print(f"🎉 HOÀN TẤT! Tổng ô đã bán: {total_slots_sold} | Tổng số lượng: {total_qty_sold} | Bạc thu về: +{total_silver_gained:,} Bạc | Thời gian: {total_time:.1f}s")
    print("=" * 110 + "\n", flush=True)

    all_sold_items = {}
    all_sold_silver = {}
    all_sold_slots = {}
    details_per_acc = []

    for acc in accounts:
        username = acc[0]
        res = results_by_user.get(username)
        if not res:
            continue
        for cr in res.get("characters_results", []):
            if cr.get("status") in ("ĐÃ BÁN", "ĐÃ XÓA"):
                c_name = cr.get("name", "")
                c_map = cr.get("sold_items_map", cr.get("deleted_items_map", {}))
                c_silver_map = cr.get("sold_items_silver", {})
                c_slots_map = cr.get("sold_items_slots", {})
                for iname, iqty in c_map.items():
                    all_sold_items[iname] = all_sold_items.get(iname, 0) + iqty
                    all_sold_silver[iname] = all_sold_silver.get(iname, 0) + c_silver_map.get(iname, 0)
                    all_sold_slots[iname] = all_sold_slots.get(iname, 0) + c_slots_map.get(iname, 1)
                details_per_acc.append({
                    "username": username,
                    "char_name": c_name,
                    "deleted_slots": cr.get("sold_slots", cr.get("deleted_slots", 0)),
                    "deleted_qty": cr.get("sold_qty", cr.get("deleted_qty", 0)),
                    "sold_slots": cr.get("sold_slots", cr.get("deleted_slots", 0)),
                    "sold_qty": cr.get("sold_qty", cr.get("deleted_qty", 0)),
                    "silver_gained": cr.get("silver_gained", 0),
                    "items": list(cr.get("sold_names", cr.get("deleted_names", [])))
                })

    all_sold_details = {}
    for iname, iqty in all_sold_items.items():
        all_sold_details[iname] = {
            "qty": iqty,
            "slots": all_sold_slots.get(iname, 0),
            "silver": all_sold_silver.get(iname, 0)
        }

    return {
        "results": all_results,
        "total_slots": total_slots_sold,
        "total_qty": total_qty_sold,
        "total_silver": total_silver_gained,
        "deleted_items_summary": all_sold_items,
        "sold_items_summary": all_sold_items,
        "sold_items_details": all_sold_details,
        "details_per_acc": details_per_acc
    }

# Aliases
run_all_item_seller = run_all_item_deleter
ItemSellerManager = ItemDeleterManager
ItemSellerRunner = ItemDeleterRunner
