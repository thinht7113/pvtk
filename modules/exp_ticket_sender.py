import time
import threading
from typing import List, Tuple, Dict, Any, Optional

from core.network import PVTKConnection
from core.message import Message
from core.config import settings
from modules.mail_sender import MailManager, parse_player_id
from modules.base_runner import CharacterLoopRunner

print_lock = threading.Lock()

def safe_print(msg: str):
    with print_lock:
        print(msg)


class Item:
    """Represents a game item deserialized from PVTK protocol (Item.smali)."""
    def __init__(self):
        self.quantity: int = 0
        self.durability: int = 0
        self.attachDone: int = 0
        self.attachPower: int = 0
        self.attachValue: int = 0
        self.slotPos: int = 0
        self.status: int = 0
        self.type: int = 0
        self.id: int = 0
        self.name: str = ""
        self.bagIcon: int = 0
        self.info: str = ""
        self.grade: int = 0
        self.autoBinding: int = 0
        self.stackNum: int = 0
        self.icon: int = 0
        self.reqLv: int = 0
        self.vipLevelReq: int = 0
        self.price: int = 0

    def getItemClass(self) -> int:
        t = self.type
        if t == 32: # 0x20
            return 3
        if t == 30: # 0x1E
            return 6
        if t in (25, 29, 33): # 0x19, 0x1D, 0x21
            return 5
        if (13 <= t <= 24) or (34 <= t <= 36):
            return 1
        if (26 <= t <= 28) or t == 31:
            return 4
        return 2

    def is_locked(self) -> bool:
        """Checks if item is bound/locked matching Item.isBinded() in Smali."""
        return self.autoBinding != 0 or (self.status & 2) != 0

    @classmethod
    def from_bytes(cls, msg: Message) -> 'Item':
        item = cls()
        # Atts1
        item.quantity = msg.getShort()
        item.durability = msg.getShort()
        item.attachDone = msg.getByte()
        item.attachPower = msg.getByte()
        item.attachValue = msg.getShort()
        item.slotPos = msg.getShort()
        item.status = msg.getByte()

        # isTimeItem: (status & 1) > 0
        if (item.status & 1) > 0:
            if item.slotPos == 17:
                item.expireTime = msg.getInt()
            else:
                item.expireTime = msg.getShort() & 0xFFFF

        # Atts2
        item.type = msg.getByte()
        item_class = item.getItemClass()

        if item_class == 3:
            item.id = 24
            item.name = msg.getString()
            item.bagIcon = msg.getByte()
            item.info = msg.getString()
            item.grade = msg.getByte()
            item.autoBinding = msg.getByte()
            item.icon = msg.getShort()
            item.reqLv = msg.getByte()
            item.vipLevelReq = msg.getByte()
            return item

        item.id = msg.getShort() & 0xFFFF
        item.name = msg.getString()
        item.bagIcon = msg.getByte()
        item.info = msg.getString()
        item.grade = msg.getByte()
        item.autoBinding = msg.getByte()
        item.stackNum = msg.getByte()

        if item_class == 1:
            item.icon = msg.getShort()
            item.ownTime = msg.getShort() & 0xFFFF
            item.durMax = msg.getByte()
            item.price = msg.getInt()
            item.attachCount = msg.getByte()
            item.itemSet = msg.getByte()
            item.reqLv = msg.getByte()
            item.reqStr = msg.getShort()
            item.reqCon = msg.getShort()
            item.reqAgi = msg.getShort()
            item.reqIlt = msg.getShort()
            item.reqWis = msg.getShort()
            cls._parse_ascension_star_info(item, msg)
            item.hitrate = msg.getByte()
            item.vipLevelReq = msg.getByte()
            item.enchantPower1 = msg.getShort()
            item.enchantPowerValue1 = msg.getShort()
            item.enchantPower2 = msg.getShort()
            item.enchantPowerValue2 = msg.getShort()
            item.isUpgradeItem = msg.getBoolean()
            if item.isUpgradeItem:
                item.power4 = msg.getShort()
                item.powerValue4 = msg.getShort()
                item.power5 = msg.getShort()
                item.powerValue5 = msg.getShort()
                item.power6 = msg.getShort()
                item.powerValue6 = msg.getShort()
                item.power7 = msg.getShort()
                item.powerValue7 = msg.getShort()
            item.upgradeAscensionStar = msg.getByte()
            item.upgradeStar = msg.getByte()
        elif item_class == 2:
            item.icon = msg.getShort()
            item.ownTime = msg.getShort() & 0xFFFF
            item.durMax = msg.getByte()
            item.price = msg.getInt()
            item.attachCount = msg.getByte()
            item.itemSet = msg.getByte()
            item.reqLv = msg.getByte()
            item.reqStr = msg.getShort()
            item.reqCon = msg.getShort()
            item.reqAgi = msg.getShort()
            item.reqIlt = msg.getShort()
            item.reqWis = msg.getShort()
            cls._parse_ascension_star_info(item, msg)
            if item.type == 12: # fashion
                item.fashIcon1 = msg.getInt()
                item.fashIcon2 = msg.getInt()
                item.fashIcon3 = msg.getInt()
            item.vipLevelReq = msg.getByte()
            item.enchantPower1 = msg.getShort()
            item.enchantPowerValue1 = msg.getShort()
            item.enchantPower2 = msg.getShort()
            item.enchantPowerValue2 = msg.getShort()
            item.isUpgradeItem = msg.getBoolean()
            if item.isUpgradeItem:
                item.power4 = msg.getShort()
                item.powerValue4 = msg.getShort()
                item.power5 = msg.getShort()
                item.powerValue5 = msg.getShort()
                item.power6 = msg.getShort()
                item.powerValue6 = msg.getShort()
                item.power7 = msg.getShort()
                item.powerValue7 = msg.getShort()
            item.upgradeAscensionStar = msg.getByte()
            item.upgradeStar = msg.getByte()
        elif item_class == 4:
            item.reqLv = msg.getByte()
            item.ownTime = msg.getShort() & 0xFFFF
            item.durMax = msg.getByte() & 0xFF
            item.price = msg.getInt()
            item.round = msg.getByte()
            item.area = msg.getByte()
            item.power1 = msg.getByte() & 0xFF
            item.powerValue1 = msg.getShort()
            item.power2 = msg.getByte() & 0xFF
            item.powerValue2 = msg.getShort()
            item.vipLevelReq = msg.getByte()
            if (item.power1 & 0xFF) == 0xB5: # 181 (Đạo Cụ Phù Ma)
                item.power3 = msg.getByte() & 0xFF
                item.powerValue3 = msg.getShort()
        elif item_class == 6:
            item.price = msg.getInt()
            item.power1 = msg.getByte() & 0xFF
            item.powerValue1 = msg.getShort()
        elif item_class in (5, 7):
            item.price = msg.getInt()

        return item

    @classmethod
    def _parse_ascension_star_info(cls, item: 'Item', msg: Message):
        item.def_str = msg.getShort()
        item.def_agi = msg.getShort()
        item.def_mag = msg.getShort()
        item.power1 = msg.getByte() & 0xFF
        item.powerValue1 = msg.getShort()
        item.power2 = msg.getByte() & 0xFF
        item.powerValue2 = msg.getShort()
        item.power3 = msg.getByte() & 0xFF
        item.powerValue3 = msg.getShort()
        item.skillDesc = msg.getString()
        item.bindPower1 = msg.getByte() & 0xFF
        item.bindPowerValue1 = msg.getShort()
        item.bindPower2 = msg.getByte() & 0xFF
        item.bindPowerValue2 = msg.getShort()
        if item.getItemClass() == 1:
            item.atkType = msg.getByte()
            item.atk_time = msg.getByte()
            item.atkMin = msg.getShort()
            item.atkMax = msg.getShort()
        item.ascensionStar = msg.getByte()
        item.star = msg.getByte()


class ExpTicketSender:
    """Manages inventory inspection and mailing of unlocked EXP tickets."""

    @staticmethod
    def get_bag_items(conn: PVTKConnection) -> List[Item]:
        """Requests and parses player bag items using 0x2EE3 (CG_BAG_CLEAN / REFRESH)."""
        while conn.receive_message(timeout=0.01):
            pass

        msg = Message(0x2EE3)
        msg.putInt(0x10) # Block 16: Items
        conn.send_message(msg)

        start_time = time.time()
        while time.time() - start_time < 4.5:
            res = conn.receive_message(timeout=1.0)
            if not res or res.type != 0x2EE3:
                continue

            try:
                code = res.getByte()
                if code != 0:
                    continue 

                new_map = res.getInt()
                if new_map >= 20000:
                    res.getShort()
                gx = res.getByte()
                gy = res.getByte()
                block_flag = res.getInt()
                
                if (block_flag & 0x10) == 0:
                    continue

                for i in range(32):
                    bid = block_flag & (1 << i)
                    if bid == 0:
                        continue
                    if bid == 0x02: # Block 2: Money & stats
                        _cid = res.getInt()
                        _name = res.getString()
                        res.offset += 76
                        _gold = res.getInt()
                        _leaf = res.getInt()
                        _silver = res.getInt()
                    elif bid == 0x10:
                        count = res.getShort()
                        items = []
                        for _ in range(count):
                            try:
                                it = Item.from_bytes(res)
                                items.append(it)
                            except Exception:
                                pass
                        return items
                    else:
                        break
            except Exception as e:
                safe_print(f"  [LỖI ĐỌC TÚI ĐỒ] {e}")
                continue

        return []

    @staticmethod
    def strip_accents(s: str) -> str:
        if not s:
            return ""
        import unicodedata
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.replace('đ', 'd').replace('Đ', 'D').lower().strip()

    @staticmethod
    def filter_unlocked_items(items: List[Item], keywords: List[str]) -> List[Item]:
        """Filters items to find ONLY unlocked items matching ANY of the keywords located in bag (slot >= 30)."""
        unlocked = []
        clean_kws = [k.strip().lower() for k in keywords if k.strip()]
        stripped_kws = [ExpTicketSender.strip_accents(k) for k in clean_kws]

        for it in items:
            if it.slotPos < 30:
                continue
            name_raw = (it.name or "").strip()
            name_lower = name_raw.lower()
            name_stripped = ExpTicketSender.strip_accents(name_raw)

            # Khớp trực tiếp hoặc khớp không phân biệt dấu tiếng Việt
            matched = any(kw in name_lower for kw in clean_kws) or any(skw in name_stripped for skw in stripped_kws)
            if matched:
                if not it.is_locked() and it.quantity > 0:
                    unlocked.append(it)
        return unlocked

    @staticmethod
    def send_single_mail_with_attachments(
        conn: PVTKConnection,
        receiver_target: Any,
        attached_items: List[Item],
        content: str = "Vé EXP Nhân Vật"
    ) -> Tuple[bool, str]:
        """Sends a mail with up to 3 attached item slots to target recipient (ID, UID e.g. 'p108226', or Name)."""
        if not attached_items:
            return False, "Không có vật phẩm đính kèm"

        items_to_send = attached_items[:3]
        target_pid = parse_player_id(str(receiver_target))

        for attempt in range(2):
            try:
                msg = Message(0x2B01)
                msg.putString(content)
                msg.putInt(0) 
                msg.putInt(0) 
                msg.putInt(0) 
                msg.putInt(0) 
                msg.putInt(0) 
                msg.putInt(0) 

                msg.putByte(len(items_to_send))
                for it in items_to_send:
                    msg.putShort(it.slotPos)
                    msg.putInt(it.id)
                    qty = min(it.quantity, 99)
                    msg.putByte(qty)

                if target_pid is not None:
                    msg.putByte(1)
                    msg.putInt(target_pid)
                else:
                    msg.putByte(2)
                    msg.putString(str(receiver_target).strip())

                res = conn.send_and_wait(msg, expected_type=0x2B01, timeout=4.5)
                if not res:
                    if attempt == 0:
                        time.sleep(0.3)
                        continue
                    return False, "Không nhận được phản hồi từ Server"

                code = res.getByte()
                if code < 0:
                    err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi: {code}"
                    return False, err_msg

                time.sleep(0.15)
                while conn.receive_message(timeout=0.05):
                    pass

                return True, "Gửi thư thành công"

            except Exception as e:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                return False, f"Ngoại lệ gửi thư: {e}"

        return False, "Gửi thư thất bại sau 2 lần thử"

    @staticmethod
    def send_all_unlocked_items(
        conn: PVTKConnection,
        char_tag: str,
        receiver_target: Any,
        unlocked_items: List[Item],
        content: str = "Vật phẩm gửi tặng"
    ) -> Tuple[int, int, str]:
        """Sends all unlocked items across one or multiple mails (3 slots/mail)."""
        if not unlocked_items:
            return 0, 0, "Không có vật phẩm không khóa"

        total_sent_tickets = 0
        total_sent_mails = 0
        
        chunks = [unlocked_items[i:i+3] for i in range(0, len(unlocked_items), 3)]

        for chunk_idx, chunk in enumerate(chunks):
            chunk_qty = sum(it.quantity for it in chunk)
            slot_info = ", ".join([f"Ô {it.slotPos} ({it.quantity} vé)" for it in chunk])
            
            ok, msg_res = ExpTicketSender.send_single_mail_with_attachments(
                conn, receiver_target, chunk, content=content
            )

            # If failed due to insufficient silver fee, auto-sell herb and retry
            if not ok and any(w in msg_res.lower() for w in ["tiền không đủ", "không đủ", "bạc", "money", "phí"]):
                safe_print(f"{char_tag} [CẢNH BÁO] Thiếu Bạc để gửi thư ({msg_res}). Đang tự động bán 'Dược Thảo Hoạt Lực'...")
                sold = MailManager.auto_sell_herb(conn)
                if sold:
                    safe_print(f"{char_tag} \033[92m[BÁN THÀNH CÔNG]\033[0m Đã có Bạc. Đang gửi lại thư đính kèm...")
                    time.sleep(0.3)
                    fresh_items = ExpTicketSender.get_bag_items(conn)
                    # We just use the same names in chunk to re-filter
                    item_names = list(set(it.name.lower() for it in chunk))
                    fresh_items_filtered = ExpTicketSender.filter_unlocked_items(fresh_items, item_names)
                    if fresh_items_filtered:
                        fresh_chunk = fresh_items_filtered[:3]
                        ok, msg_res = ExpTicketSender.send_single_mail_with_attachments(
                            conn, receiver_target, fresh_chunk, content=content
                        )

            if ok:
                total_sent_mails += 1
                total_sent_tickets += chunk_qty
                safe_print(f"{char_tag} \033[92m[GỬI THƯ THÀNH CÔNG - LẦN {chunk_idx + 1}]\033[0m Đã đính kèm {chunk_qty} Vật phẩm ({slot_info}) -> Người nhận: '{receiver_target}'")
            else:
                safe_print(f"{char_tag} \033[91m[GỬI THƯ THẤT BẠI]\033[0m {msg_res}")
                return total_sent_tickets, total_sent_mails, msg_res

            time.sleep(0.3)

        return total_sent_tickets, total_sent_mails, "Thành công"


class ExpTicketSenderRunner(CharacterLoopRunner):
    def __init__(self, accounts: List[Tuple], thread_name: str = "Tài khoản", max_workers: int = None, max_mails: Optional[int] = None, stop_event: Optional[threading.Event] = None):
        super().__init__(accounts, thread_name=thread_name, max_workers=max_workers, stop_event=stop_event)
        self.max_mails = max_mails
        self.quota_reached = False
        self.quota_lock = threading.Lock()
        self.sent_mails = 0
        self.sent_tickets = 0
        self.reserved_mails = 0

    def acquire_mail_permit(self) -> bool:
        if self.is_stopped():
            return False
        with self.quota_lock:
            if self.max_mails is not None and (self.sent_mails + self.reserved_mails) >= self.max_mails:
                return False
            self.reserved_mails += 1
            return True

    def release_mail_permit(self, success: bool = False, count: int = 1, tickets: int = 0):
        with self.quota_lock:
            self.reserved_mails = max(0, self.reserved_mails - 1)
            if success:
                self.sent_mails += count
                self.sent_tickets += tickets
                if self.max_mails is not None and self.sent_mails >= self.max_mails:
                    self.quota_reached = True
                    self.stop_event.set()

    def should_process_character(self, char, task: Tuple) -> bool:
        if self.is_stopped():
            return False
        receiver_target = str(task[5]).strip()
        target_pid = parse_player_id(receiver_target)
        
        is_self = (target_pid is not None and char.id == target_pid) or (char.name.lower() == receiver_target.lower())
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        if self.is_stopped():
            return

        receiver_target = str(task[5]).strip()
        recipient_name = task[6]
        keywords = task[7] if len(task) > 7 else ["vé exp nhân vật", "ve exp nhan vat"]
        mail_title = task[8] if len(task) > 8 else "Gửi Vé EXP Nhân Vật"
        target_pid = parse_player_id(receiver_target)
        
        is_self = (target_pid is not None and char.id == target_pid) or (char.name.lower() == receiver_target.lower())
        if is_self:
            self.log(f"{char_tag} [BỎ QUA] Đây là chính nhân vật nhận thư ('{receiver_target}').")
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "sent_tickets": 0,
                "sent_mails": 0,
                "status": "SKIP_SELF",
                "message": "Chính nhân vật người nhận"
            })
            return

        bag_items = ExpTicketSender.get_bag_items(game_conn)
        unlocked_items = ExpTicketSender.filter_unlocked_items(bag_items, keywords)

        total_unlocked_qty = sum(it.quantity for it in unlocked_items)

        if total_unlocked_qty == 0:
            self.log(f"{char_tag} \033[90m[KHÔNG CÓ VẬT PHẨM]\033[0m Túi đồ không có vật phẩm mục tiêu không khóa.")
            account_result["characters_results"].append({
                "name": char.name,
                "id": char.id,
                "sent_tickets": 0,
                "sent_mails": 0,
                "status": "NO_ITEMS",
                "message": "Không có vật phẩm mục tiêu"
            })
            return

        chunks = [unlocked_items[i:i+3] for i in range(0, len(unlocked_items), 3)]
        char_sent_tickets = 0
        char_sent_mails = 0
        last_msg = "Thành công"

        for chunk_idx, chunk in enumerate(chunks):
            if self.is_stopped():
                break

            if not self.acquire_mail_permit():
                self.log(f"{char_tag} \033[93m[DỪNG - ĐÃ ĐẠT GIỚI HẠN]\033[0m Đã gửi đủ số thư yêu cầu ({self.max_mails} thư). Bỏ qua gửi thêm.")
                if not last_msg or last_msg == "Thành công":
                    last_msg = f"Đã đạt giới hạn {self.max_mails} thư"
                break

            chunk_qty = sum(it.quantity for it in chunk)
            slot_info = ", ".join([f"Ô {it.slotPos} ({it.quantity} món)" for it in chunk])

            ok, msg_res = ExpTicketSender.send_single_mail_with_attachments(
                game_conn, receiver_target, chunk, content=mail_title
            )

            # If failed due to insufficient silver fee, auto-sell herb and retry
            if not ok and any(w in msg_res.lower() for w in ["tiền không đủ", "không đủ", "bạc", "money", "phí"]):
                safe_print(f"{char_tag} [CẢNH BÁO] Thiếu Bạc để gửi thư ({msg_res}). Đang tự động bán 'Dược Thảo Hoạt Lực'...")
                sold = MailManager.auto_sell_herb(game_conn)
                if sold:
                    safe_print(f"{char_tag} \033[92m[BÁN THÀNH CÔNG]\033[0m Đã có Bạc. Đang gửi lại thư đính kèm...")
                    time.sleep(0.3)
                    fresh_items = ExpTicketSender.get_bag_items(game_conn)
                    item_names = list(set(it.name.lower() for it in chunk))
                    fresh_items_filtered = ExpTicketSender.filter_unlocked_items(fresh_items, item_names)
                    if fresh_items_filtered:
                        fresh_chunk = fresh_items_filtered[:3]
                        ok, msg_res = ExpTicketSender.send_single_mail_with_attachments(
                            game_conn, receiver_target, fresh_chunk, content=mail_title
                        )

            if ok:
                self.release_mail_permit(success=True, count=1, tickets=chunk_qty)
                char_sent_mails += 1
                char_sent_tickets += chunk_qty
                safe_print(f"{char_tag} \033[92m[GỬI THƯ THÀNH CÔNG - LẦN {chunk_idx + 1}]\033[0m Đã đính kèm {chunk_qty} Vật phẩm ({slot_info}) -> Người nhận: '{receiver_target}'")
            else:
                self.release_mail_permit(success=False)
                safe_print(f"{char_tag} \033[91m[GỬI THƯ THẤT BẠI]\033[0m {msg_res}")
                last_msg = msg_res
                break

            time.sleep(0.3)

        status_str = "SUCCESS" if char_sent_tickets > 0 else ("LIMIT_REACHED" if self.is_stopped() else "FAILED")
        account_result["characters_results"].append({
            "name": char.name,
            "id": char.id,
            "sent_tickets": char_sent_tickets,
            "sent_mails": char_sent_mails,
            "status": status_str,
            "message": last_msg
        })


def run_all_exp_ticket_sender(
    accounts: List[Tuple], 
    target: str, 
    recipient_name: str = None, 
    max_workers: int = None,
    keywords: List[str] = ["vé exp nhân vật", "ve exp nhan vat"],
    mail_title: str = "Gửi Vé EXP Nhân Vật",
    log_title: str = "VÉ EXP NHÂN VẬT",
    max_mails: Optional[int] = None,
    stop_event: Optional[threading.Event] = None
) -> Dict[str, Any]:
    receiver_target = target
    total_accs = len(accounts)
    name_display = f" ({recipient_name})" if recipient_name else ""
    
    # Điều chỉnh số luồng hợp lý nếu có max_mails
    effective_workers = max_workers
    if max_mails is not None and max_mails > 0:
        suggested_workers = min(total_accs, max(3, max_mails + 3))
        if effective_workers is None or effective_workers > suggested_workers:
            effective_workers = suggested_workers
    elif effective_workers is None:
        effective_workers = total_accs

    limit_info = f"{max_mails} thư" if max_mails is not None and max_mails > 0 else "Không giới hạn (Toàn bộ)"

    print("\n" + "=" * 90)
    print(f"  BẮT ĐẦU QUÉT & GỬI {log_title} (KHÔNG KHÓA) CHO {total_accs} TÀI KHOẢN")
    print(f"  NGƯỜI NHẬN: '{receiver_target}'{name_display} | GIỚI HẠN: {limit_info} | ĐA LUỒNG: {effective_workers}")
    print("=" * 90 + "\n")

    start_time = time.time()
    
    account_tuples = []
    for idx, acc in enumerate(accounts, 1):
        username = acc[0]
        password = acc[1]
        proxy = acc[2] if len(acc) >= 3 else None
        account_tuples.append((idx, total_accs, username, password, proxy, receiver_target, recipient_name, keywords, mail_title))

    runner = ExpTicketSenderRunner(
        account_tuples,
        thread_name="Tài khoản",
        max_workers=effective_workers,
        max_mails=max_mails,
        stop_event=stop_event
    )
    all_results = runner.run_all()
    
    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}

    total_time = time.time() - start_time
    total_tickets_sent = 0
    total_mails_sent = 0

    print("\n" + "=" * 105)
    print(f"               BẢNG TỔNG KẾT GỬI {log_title} (KHÔNG KHÓA)")
    print("=" * 105)
    print(f"{'TÀI KHOẢN':<15} | {'TÊN NHÂN VẬT':<18} | {'VẬT PHẨM GỬI':<12} | {'SỐ THƯ':<8} | {'TRẠNG THÁI':<20} | {'GHI CHÚ'}")
    print("-" * 105)

    for acc in accounts:
        username = acc[0]
        acc_res = results_by_user.get(username, {"username": username, "error": "Chưa hoàn thành"})
        if acc_res.get("error"):
            print(f"{username:<15} | {'-':<18} | {'0':<12} | {'0':<8} | \033[91m{'LỖI':<20}\033[0m | {acc_res['error']}")
            continue

        chars_res = acc_res.get("characters_results", [])
        if not chars_res:
            print(f"{username:<15} | {'-':<18} | {'0':<12} | {'0':<8} | \033[90m{'KHÔNG CÓ NV':<20}\033[0m | -")
            continue

        for cr in chars_res:
            cname = cr.get("name", "-")
            sent_tk = cr.get("sent_tickets", 0)
            sent_ml = cr.get("sent_mails", 0)
            status = cr.get("status", "-")
            msg = cr.get("message", "-")

            total_tickets_sent += sent_tk
            total_mails_sent += sent_ml

            if status == "SUCCESS":
                status_colored = f"\033[92m{f'ĐÃ GỬI ({sent_tk} MÓN)':<20}\033[0m"
            elif status == "NO_ITEMS":
                status_colored = f"\033[90m{'KHÔNG CÓ VẬT PHẨM':<20}\033[0m"
            elif status == "SKIP_SELF":
                status_colored = f"\033[94m{'BỎ QUA (NGƯỜI NHẬN)':<20}\033[0m"
            elif status == "LIMIT_REACHED":
                status_colored = f"\033[93m{'ĐÃ ĐẠT GIỚI HẠN':<20}\033[0m"
            else:
                status_colored = f"\033[91m{'THẤT BẠI':<20}\033[0m"

            print(f"{username:<15} | {cname:<18} | {sent_tk:<12} | {sent_ml:<8} | {status_colored} | {msg}")

    final_mails = max(total_mails_sent, runner.sent_mails)
    final_tickets = max(total_tickets_sent, runner.sent_tickets)
    quota_reached = getattr(runner, "quota_reached", False) or (max_mails is not None and final_mails >= max_mails)
    user_cancelled = runner.is_user_cancelled() and not quota_reached

    print("-" * 105)
    print(f"-> TỔNG SỐ VẬT PHẨM ĐÃ GỬI: \033[92m{final_tickets:,}\033[0m | TỔNG SỐ THƯ: \033[93m{final_mails} Thư\033[0m (Giới hạn: {limit_info}) -> ĐẾN: '{receiver_target}'{name_display}")
    print(f"-> Tổng thời gian hoàn thành: {total_time:.1f} giây")
    print("=" * 105 + "\n")

    return {
        "total_tickets": final_tickets,
        "total_mails": final_mails,
        "total_time": round(total_time, 1),
        "elapsed": round(total_time, 1),
        "quota_reached": quota_reached,
        "cancelled": user_cancelled
    }


def run_test_item_mail(
    accounts: List[Tuple],
    target_str: str,
    keywords: List[str] = ["vé exp nhân vật", "ve exp nhan vat"],
    item_name_display: str = "vật phẩm",
    max_workers: int = 15,
    stop_event: Optional[threading.Event] = None
) -> bool:
    """
    Quét đa luồng song song qua các tài khoản để tìm nhân vật đầu tiên có vật phẩm thỏa mãn (không khóa).
    Gửi ĐÚNG 1 thư test chứa 1 vật phẩm đến target_str.
    Trả về True nếu gửi thành công, False nếu không tìm thấy hoặc bị hủy.
    """
    from core.config import settings
    from modules.free_proxy import FreeProxyManager
    from modules.auth import AuthManager
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import copy

    if stop_event is None:
        stop_event = threading.Event()

    found_lock = threading.Lock()
    test_sent = threading.Event()

    def _check_acc(acc):
        if stop_event.is_set() or test_sent.is_set():
            return False

        username = acc[0]
        password = acc[1]
        acc_proxy = acc[4] if len(acc) >= 5 and acc[4] else None
        proxies_to_try = [acc_proxy, None] if acc_proxy else [None, FreeProxyManager.get_random_proxy()]

        for proxy in proxies_to_try:
            if stop_event.is_set() or test_sent.is_set():
                return False
            game_conn = None
            try:
                login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, username, password, proxy=proxy)
                if not login_data:
                    continue
                ukey, session_id, login_setting = login_data
                game_conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, ukey, session_id, proxy=proxy)
                if not game_conn:
                    continue
                chars = AuthManager.fetch_character_list(game_conn)
                if not chars:
                    continue

                for char in chars:
                    if stop_event.is_set() or test_sent.is_set():
                        break
                    if str(char.id) == target_str or char.name == target_str:
                        continue

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(game_conn, char.id, login_setting)
                    except Exception:
                        pass
                    if not enter_ok:
                        continue

                    bag_items = ExpTicketSender.get_bag_items(game_conn)
                    unlocked_items = ExpTicketSender.filter_unlocked_items(bag_items, keywords)

                    if unlocked_items:
                        with found_lock:
                            if test_sent.is_set():
                                try:
                                    AuthManager.leave_character(game_conn)
                                except Exception:
                                    pass
                                return False

                            test_ticket = unlocked_items[0]
                            single_ticket = copy.copy(test_ticket)
                            single_ticket.quantity = 1

                            safe_print(f"  \033[92m[TÌM THẤY]\033[0m Lấy 1 {item_name_display} từ nhân vật '{char.name}' (Acc: {username}). Đang gửi thư test...")
                            sent_qty, sent_mails, msg_res = ExpTicketSender.send_all_unlocked_items(
                                game_conn, "[TEST MAIL]", target_str, [single_ticket], content=f"Thư Test {item_name_display}"
                            )
                            try:
                                AuthManager.leave_character(game_conn)
                            except Exception:
                                pass

                            if sent_qty > 0:
                                test_sent.set()
                                safe_print(f"  \033[92m[GỬI TEST THÀNH CÔNG]\033[0m Đã gửi 1 thư test đến '{target_str}'. Vui lòng kiểm tra!")
                                return True
                            else:
                                safe_print(f"  \033[91m[GỬI TEST THẤT BẠI]\033[0m Lỗi: {msg_res}")
                                return False

                    try:
                        AuthManager.leave_character(game_conn)
                    except Exception:
                        pass

                break # Acc login ok
            except Exception:
                continue
            finally:
                if game_conn:
                    try:
                        game_conn.close()
                    except Exception:
                        pass
        return False

    eff_workers = min(len(accounts), max(3, max_workers))
    with ThreadPoolExecutor(max_workers=eff_workers) as executor:
        futures = [executor.submit(_check_acc, acc) for acc in accounts]
        for f in as_completed(futures):
            if test_sent.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return True
            if stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return False

    return test_sent.is_set()
