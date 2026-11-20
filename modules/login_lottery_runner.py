"""
PVTK Login Lottery Draw Automation Module (Rút Thưởng Đăng Nhập / Login Lottery Wheel).
Protocol:
- Opcode 0x2B62: Request Login Lottery Info & 16 Wheel Items
- Opcode 0x2B63: Roll (Byte 0 = FREE_LOTTERY, Byte 1 = USE_GOLD_LOTTERY)

Workflow:
1. Log in to Account & Enter Character (Level >= 35 only).
2. Drain TCP buffer to ensure clean message state.
3. Fetch Lottery Status (0x2B62) -> verifies freeCount and item wheel.
4. Auto-Roll (0x2B63 with byte 0) if freeCount > 0.
5. Parse winning slot (focus_index 0..15) and awarded item name.
6. Persist to DailyHistoryManager (storage/login_lottery_history.json).
7. Report summary to console and Telegram.
"""

import os
import sys
import json
import time
import threading
from datetime import datetime, timezone, timedelta
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
from modules.base_runner import BaseAccountRunner
from modules.free_proxy import FreeProxyManager
from modules.daily_history import DailyHistoryManager

PRINT_LOCK = threading.Lock()

def log(msg: str):
    with PRINT_LOCK:
        print(msg, flush=True)


FALLBACK_WHEEL = {
    0: "Xích Thố (5 ngày)",
    1: "Dược Thảo Hoạt Lực",
    2: "Rương Vật Liệu I (Gỗ)",
    3: "Dược Thảo Hoạt Lực",
    4: "Vé EXP Pet (30000)",
    5: "Đá +1 Nhanh Nhẹn (Khóa)",
    6: "Đá +2 Thể Lực (Khóa)",
    7: "Thuốc Sức Mạnh (1 Giờ)",
    8: "Dược Thảo Hoạt Lực",
    9: "Hộp Sách Tu Chân",
    10: "Dược Thảo Hoạt Lực",
    11: "Dược Thảo Hoạt Lực",
    12: "Sư Thứu Thần Thánh Full Thuộc Tính (1 Ngày)",
    13: "Dược Thảo Hoạt Lực",
    14: "Vé EXP Pet (30000)",
    15: "Kim Diệp x 5"
}


class LoginLotteryManager:
    @staticmethod
    def parse_wheel_items(msg: Message) -> Dict[int, str]:
        """
        Dynamically parses all 16 slots from the 0x2B62 packet payload.
        Mirrors client com.hz.core.LoginLotteryDraw and com.hz.core.Item.
        Returns a dict mapping slot_index (0..15) -> item_name.
        """
        wheel_items = dict(FALLBACK_WHEEL)
        try:
            if not msg or not msg.hasRemaining():
                return wheel_items
            size = msg.getByte()
            for i in range(size):
                if not msg.hasRemaining():
                    break
                is_item = msg.getBoolean()
                if not is_item:
                    # Non-item reward (e.g. Kim Diệp, Đồng, EXP)
                    msg.getInt()  # type
                    msg.getInt()  # value
                    desc2 = msg.getString()
                    if desc2:
                        clean_desc = desc2.replace("（", " (").replace("）", ")").strip()
                        clean_desc = clean_desc.replace("  (", " (")
                        wheel_items[i] = clean_desc
                    continue

                is_has_reward = msg.getBoolean()
                if not is_has_reward:
                    continue

                itype = msg.getByte()
                iclass = 2
                if itype == 32:
                    iclass = 3
                elif itype == 30:
                    iclass = 6
                elif itype in (25, 29, 33):
                    iclass = 5
                elif (13 <= itype <= 24) or (34 <= itype <= 36):
                    iclass = 1
                elif (26 <= itype <= 28) or itype == 31:
                    iclass = 4

                if iclass == 3:
                    iname = msg.getString()
                    msg.getByte()   # ibagicon
                    msg.getString() # iinfo
                    msg.getByte()   # igrade
                    msg.getByte()   # iauto
                    msg.getShort()  # iicon
                    msg.getByte()   # ireqlv
                    msg.getByte()   # ivip
                else:
                    msg.getShort()  # iid
                    iname = msg.getString()
                    msg.getByte()   # ibagicon
                    msg.getString() # iinfo
                    msg.getByte()   # igrade
                    msg.getByte()   # iauto
                    msg.getByte()   # istack

                    if iclass in (1, 2):
                        msg.getShort() # iicon
                        msg.getShort() # ownTime
                        msg.getByte()  # durMax
                        msg.getInt()   # price
                        msg.getByte()  # attachCount
                        msg.getByte()  # itemSet
                        msg.getByte()  # reqLv
                        msg.getShort(); msg.getShort(); msg.getShort(); msg.getShort(); msg.getShort() # req stats
                        # fromItemAscensionStarInfo
                        msg.getShort(); msg.getShort(); msg.getShort() # def_str, def_agi, def_mag
                        msg.getByte(); msg.getShort() # power1, val1
                        msg.getByte(); msg.getShort() # power2, val2
                        msg.getByte(); msg.getShort() # power3, val3
                        msg.getString() # skillDesc
                        msg.getByte(); msg.getShort() # bindPower1, val1
                        msg.getByte(); msg.getShort() # bindPower2, val2
                        if iclass == 1:
                            msg.getByte()  # atkType
                            msg.getByte()  # atk_time
                            msg.getShort() # atkMin
                            msg.getShort() # atkMax
                        msg.getByte() # ascensionStar
                        msg.getByte() # star

                        if iclass == 2 and itype == 12:
                            msg.getInt(); msg.getInt(); msg.getInt()

                        if iclass == 1:
                            msg.getByte() # hitrate
                        msg.getByte() # vipLevelReq
                        msg.getShort(); msg.getShort(); msg.getShort(); msg.getShort() # enchant 1 & 2
                        is_upgrade = msg.getBoolean()
                        if is_upgrade:
                            for _ in range(8):
                                msg.getShort()
                        msg.getByte(); msg.getByte() # upgradeAscensionStar, upgradeStar
                    elif iclass == 4:
                        msg.getByte()  # reqLv
                        msg.getShort() # ownTime
                        msg.getByte()  # durMax
                        msg.getInt()   # price
                        msg.getByte()  # round
                        msg.getByte()  # area
                        p1 = msg.getByte()
                        msg.getShort() # pVal1
                        msg.getByte()  # p2
                        msg.getShort() # pVal2
                        msg.getByte()  # vipLevelReq
                        if (p1 & 0xff) == 0xb5:
                            msg.getByte(); msg.getShort()
                    elif iclass == 6:
                        msg.getInt()   # price
                        msg.getByte()  # power1
                        msg.getShort() # powerValue1
                    elif iclass in (5, 7):
                        msg.getInt()   # price

                # Quantity for item in LoginLotteryDraw
                msg.getShort()

                if iname:
                    clean_name = iname.replace("（", " (").replace("）", ")").strip()
                    clean_name = clean_name.replace("  (", " (")
                    wheel_items[i] = clean_name
        except Exception:
            pass
        return wheel_items

    @classmethod
    def check_and_draw(
        cls,
        conn: PVTKConnection,
        char_tag: str,
        char_level: int = 0,
        target_item: str = "Trứng Mèo Đen",
        max_reloads: int = 200,
        log_callback=None
    ) -> Dict[str, Any]:
        """
        Sends 0x2B62 to check free count and rolls 0x2B63 if count > 0.
        If char_level >= 50 and target_item is specified:
            Repeatedly reloads 0x2B62 (up to max_reloads times) until target_item appears on wheel.
            If not found after max_reloads times, proceeds to draw anyway.
        """
        result = {
            "status": "ERROR",
            "message": "",
            "drawn": False,
            "focus_index": -1,
            "item_name": "",
            "free_count": 0,
            "all_count": 0,
            "reloads_done": 0,
            "hunted_target": False,
            "target_slot": -1
        }

        # 1. Drain residual incoming packets
        conn.drain_buffer(max_duration=0.5)

        # 2. Request initial 0x2B62
        msg_req = Message(0x2B62)
        res_info = conn.send_and_wait(msg_req, expected_type=0x2B62, timeout=5.0)
        if not res_info:
            result["status"] = "ERROR"
            result["message"] = "Không nhận được phản hồi 0x2B62 từ Server"
            return result

        rs = res_info.getByte()
        if rs < 0:
            err_text = res_info.getString() if res_info.hasRemaining() else "Chức năng chưa mở"
            result["status"] = "NOT_UNLOCKED"
            result["message"] = err_text
            return result

        try:
            desc = res_info.getString()
            vipdesc = res_info.getString()
            vip_count = res_info.getByte()
            all_count = res_info.getInt()
            free_count = res_info.getInt()
            for _ in range(3):
                res_info.getString()
                res_info.getString()

            wheel_items = cls.parse_wheel_items(res_info)
        except Exception as e:
            free_count = 1
            all_count = 1
            wheel_items = dict(FALLBACK_WHEEL)

        result["free_count"] = free_count
        result["all_count"] = all_count

        if free_count <= 0:
            result["status"] = "ALREADY_DRAWN"
            result["message"] = "Hết lượt rút miễn phí hôm nay"
            return result

        # 3. Reload logic for char_level >= 50 (Hunt target item, e.g. Trứng Mèo Đen)
        def find_target(wheel: Dict[int, str], target: str) -> Tuple[bool, int, str]:
            if not target:
                return False, -1, ""
            t_norm = target.strip().lower()
            for slot_idx, iname in wheel.items():
                if t_norm in iname.lower():
                    return True, slot_idx, iname
            return False, -1, ""

        reloads_done = 0
        hunted = False
        target_slot = -1

        if char_level >= 50 and target_item:
            found, slot_idx, iname = find_target(wheel_items, target_item)
            if found:
                hunted = True
                target_slot = slot_idx
                if log_callback:
                    log_callback(f"{char_tag} 🎯 Đã có sẵn '{iname}' tại ô #{slot_idx}. Tiến hành rút ngay!")
            else:
                if log_callback:
                    log_callback(f"{char_tag} 🎯 Nhân vật Lv.{char_level} >= 50: Bắt đầu reload vòng xoay săn '{target_item}' (tối đa {max_reloads} lần)...")

                while not found and reloads_done < max_reloads:
                    reloads_done += 1
                    conn.drain_buffer(max_duration=0.05)
                    msg_reload = Message(0x2B62)
                    res_reload = conn.send_and_wait(msg_reload, expected_type=0x2B62, timeout=4.0)
                    if not res_reload:
                        time.sleep(0.05)
                        continue
                    rs_r = res_reload.getByte()
                    if rs_r < 0:
                        break
                    try:
                        res_reload.getString()
                        res_reload.getString()
                        res_reload.getByte()
                        res_reload.getInt()
                        res_reload.getInt()
                        for _ in range(3):
                            res_reload.getString()
                            res_reload.getString()
                        wheel_items = cls.parse_wheel_items(res_reload)
                        found, slot_idx, iname = find_target(wheel_items, target_item)
                        if found:
                            hunted = True
                            target_slot = slot_idx
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)

                if hunted:
                    if log_callback:
                        log_callback(f"{char_tag} \033[92m🎯 [RELOAD THÀNH CÔNG]\033[0m Đã săn được '{iname}' tại ô #{target_slot} sau {reloads_done} lần reload!")
                else:
                    if log_callback:
                        log_callback(f"{char_tag} \033[93m⚠️ [HẾT {max_reloads} LẦN RELOAD]\033[0m Không ra '{target_item}' sau {reloads_done} lần, bỏ qua điều kiện và rút luôn...")

        result["reloads_done"] = reloads_done
        result["hunted_target"] = hunted
        result["target_slot"] = target_slot

        # 4. Perform Draw: Send 0x2B63 with byte 0 (FREE_LOTTERY)
        msg_draw = Message(0x2B63)
        msg_draw.putByte(0)

        res_draw = conn.send_and_wait(msg_draw, expected_type=0x2B63, timeout=5.0)
        if not res_draw:
            result["status"] = "ERROR"
            result["message"] = "Không nhận được phản hồi kết quả rút 0x2B63 từ Server"
            return result

        rs_draw = res_draw.getByte()
        if rs_draw < 0:
            err_draw = res_draw.getString() if res_draw.hasRemaining() else "Lỗi rút thưởng"
            result["status"] = "DRAW_FAILED"
            result["message"] = err_draw
            return result

        focus_index = res_draw.getInt()
        is_item = res_draw.getBoolean()
        item_name = wheel_items.get(focus_index, FALLBACK_WHEEL.get(focus_index, f"Ô #{focus_index}"))

        result["status"] = "SUCCESS"
        result["drawn"] = True
        result["focus_index"] = focus_index
        result["item_name"] = item_name

        extra_info = ""
        if reloads_done > 0:
            extra_info = f" [Reload {reloads_done} lần]"
        if hunted and focus_index == target_slot:
            extra_info += f" 🔥 TRÚNG MỤC TIÊU SĂN: {target_item}!"

        result["message"] = f"Trúng: {item_name} (Ô #{focus_index}){extra_info}"
        return result


class LoginLotteryRunner(BaseAccountRunner):
    """
    Subclass of BaseAccountRunner for Login Lottery.
    Processes all characters with level >= min_level (default 35).
    Automatically reconnects if the socket requires a fresh session per character.
    """
    def __init__(self, accounts: List[Tuple], min_level: int = 35, max_workers: int = None):
        super().__init__(
            accounts=accounts,
            thread_name="RútLogin",
            max_retries=5,
            proxy_rotation=True,
            max_workers=max_workers
        )
        self.min_level = min_level

    def process_account(self, game_conn: PVTKConnection, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any]):
        idx = task[0]
        total = task[1]
        username = task[2]
        password = task[3]
        proxy = task[4] if len(task) >= 5 else None

        g_host = task[5] if len(task) >= 10 else settings.game_host
        g_port = task[6] if len(task) >= 10 else settings.game_port
        m_host = task[7] if len(task) >= 10 else settings.master_host
        m_port = task[8] if len(task) >= 10 else settings.master_port

        characters = AuthManager.fetch_character_list(game_conn)
        if not characters:
            account_result["error"] = "Không thể tải danh sách nhân vật"
            account_result["completed"] = False
            return

        eligible_chars = [c for c in characters if c.level >= self.min_level]
        if not eligible_chars:
            self.log(f"{thread_tag} ⚪ Không có nhân vật nào đạt cấp >= {self.min_level}. Hoàn thành.")
            account_result["completed"] = True
            account_result["error"] = ""
            return

        active_conn = game_conn
        active_login_setting = login_setting
        failed_chars = []

        try:
            for char_idx, char in enumerate(eligible_chars):
                if self.stop_event.is_set():
                    account_result["completed"] = False
                    return

                char_tag = f"{thread_tag} [{char.name} Lv.{char.level}]"

                # Check if already completed today
                if DailyHistoryManager.is_login_lottery_done_today(username, char.name):
                    self.log(f"{char_tag} \033[90m[ĐÃ RÚT HÔM NAY]\033[0m Nhân vật đã rút thưởng login hôm nay. Bỏ qua.")
                    continue

                # Ensure connection is active and enter character
                enter_ok = False
                if active_conn and active_conn.is_connected:
                    try:
                        enter_ok = AuthManager.enter_character(
                            active_conn, char.id, active_login_setting, device_id=dev.get("android_id", ""), char_info=char
                        )
                    except Exception:
                        enter_ok = False

                if not enter_ok:
                    # Reconnect clean game session for this character
                    try:
                        if active_conn:
                            try: active_conn.close()
                            except Exception: pass
                            active_conn = None
                        new_conn, new_ls = AuthManager.reconnect_game_session(
                            g_host, g_port, m_host, m_port, username, password, proxy=proxy
                        )
                        if new_conn:
                            active_conn = new_conn
                            active_login_setting = new_ls
                            enter_ok = AuthManager.enter_character(
                                active_conn, char.id, active_login_setting, device_id=dev.get("android_id", ""), char_info=char
                            )
                            if not enter_ok and active_conn:
                                try: active_conn.close()
                                except Exception: pass
                                active_conn = None
                    except Exception as e_recon:
                        self.log(f"{char_tag} [LỖI KẾT NỐI LẠI] {e_recon}")
                        if active_conn:
                            try: active_conn.close()
                            except Exception: pass
                            active_conn = None

                if not enter_ok:
                    self.log(f"{char_tag} \033[93m[KHÔNG VÀO ĐƯỢC NV]\033[0m Sẽ thử lại trong lượt chạy sau.")
                    failed_chars.append(char)
                    continue

                # Execute draw
                try:
                    time.sleep(0.5)
                    draw_res = LoginLotteryManager.check_and_draw(
                        active_conn,
                        char_tag=char_tag,
                        char_level=char.level,
                        target_item="Trứng Mèo Đen",
                        max_reloads=200,
                        log_callback=self.log
                    )
                    draw_res["char_name"] = char.name
                    draw_res["level"] = char.level
                    account_result["characters_results"].append(draw_res)

                    if draw_res["drawn"]:
                        item_name = draw_res.get("item_name", "")
                        is_excluded = DailyHistoryManager.is_excluded_login_item(item_name)
                        if "Trứng Mèo Đen" in item_name or "mèo đen" in item_name.lower():
                            self.log(f"{char_tag} \033[92m🐱🔥 [QUAY TRÚNG TRỨNG MÈO ĐEN]\033[0m {draw_res['message']} | Acc: {username}")
                        elif not is_excluded:
                            self.log(f"{char_tag} \033[92m🎉 [RÚT THÀNH CÔNG - ĐỒ QUÝ]\033[0m {draw_res['message']} | Acc: {username}")
                        else:
                            self.log(f"{char_tag} \033[90m[Rút thành công - Đồ thường]\033[0m {draw_res['message']}")
                        DailyHistoryManager.save_login_lottery_done(
                            username=username,
                            char_name=char.name,
                            level=char.level,
                            focus_index=draw_res["focus_index"],
                            item_name=item_name,
                            status="SUCCESS",
                            message=draw_res["message"]
                        )
                    elif draw_res["status"] == "ALREADY_DRAWN":
                        self.log(f"{char_tag} \033[94m[HẾT LƯỢT MIỄN PHÍ]\033[0m Nhân vật đã hết lượt rút miễn phí hôm nay.")
                        DailyHistoryManager.save_login_lottery_done(
                            username=username,
                            char_name=char.name,
                            level=char.level,
                            focus_index=-1,
                            item_name="",
                            status="ALREADY_DRAWN",
                            message=draw_res["message"]
                        )
                    elif draw_res["status"] == "NOT_UNLOCKED":
                        self.log(f"{char_tag} \033[93m[CHƯA MỞ]\033[0m {draw_res['message']}")
                        DailyHistoryManager.save_login_lottery_done(
                            username=username,
                            char_name=char.name,
                            level=char.level,
                            focus_index=-1,
                            item_name="",
                            status="NOT_UNLOCKED",
                            message=draw_res["message"]
                        )
                    else:
                        self.log(f"{char_tag} \033[91m[LỖI RÚT]\033[0m {draw_res['message']}")
                        failed_chars.append(char)

                except Exception as e_draw:
                    self.log(f"{char_tag} [LỖI NGOẠI LỆ RÚT] {e_draw}")
                    failed_chars.append(char)
                finally:
                    if active_conn:
                        try:
                            AuthManager.leave_character(active_conn)
                        except Exception:
                            pass
                    time.sleep(0.3)

        finally:
            if active_conn and active_conn is not game_conn:
                try:
                    active_conn.close()
                except Exception:
                    pass
        if not failed_chars:
            account_result["completed"] = True
            account_result["error"] = ""
        else:
            account_result["completed"] = False
            account_result["error"] = f"Còn {len(failed_chars)}/{len(eligible_chars)} nhân vật chưa hoàn thành"


def run_all_login_lottery_accounts(accounts: List[Tuple], min_level: int = 35, max_workers: int = None) -> List[Dict[str, Any]]:
    """
    Executes automated login lottery draw across all target accounts.
    """
    start_t = time.time()
    total = len(accounts)
    workers = max_workers if max_workers and max_workers > 0 else min(max(1, total), 30)

    log(f"\n{'=' * 80}")
    log(f"   BẮT ĐẦU TỰ ĐỘNG RÚT THƯỞNG LOGIN (NHÂN VẬT CẤP ĐỘ >= {min_level})")
    log(f"   Tổng số tài khoản: {total} acc | Số luồng thực thi: {workers} luồng")
    log(f"{'=' * 80}\n")

    runner = LoginLotteryRunner(accounts=accounts, min_level=min_level, max_workers=workers)
    results = runner.run_all()

    elapsed = round(time.time() - start_t, 1)
    summary = DailyHistoryManager.get_today_login_lottery_summary()

    log(f"\n{'=' * 80}")
    log(f"   BẢNG TỔNG KẾT RÚT THƯỞNG LOGIN NGÀY {datetime.now(timezone(timedelta(hours=7))).strftime('%d/%m/%Y')}")
    log(f"{'=' * 80}")
    log(f"  • Tổng nhân vật quét: {summary.get('total_scanned', 0)} NV")
    log(f"  • Nhân vật rút thành công: {summary.get('total_success', 0)} NV")
    log(f"  • Nhân vật đã rút từ trước: {summary.get('total_already_drawn', 0)} NV")
    log(f"  • Thời gian hoàn thành: {elapsed}s")

    items_list = summary.get("items_won", [])
    grouped_lines = DailyHistoryManager.format_grouped_login_items(items_list, max_chars_per_item=8, bullet="  • ", strip_html=True)
    if grouped_lines:
        total_valuable = sum(1 for it in items_list if not DailyHistoryManager.is_excluded_login_item(it.get("item_name", "")))
        log(f"\n🎁 DANH SÁCH VẬT PHẨM QUÝ ĐÃ RÚT ĐƯỢC HÔM NAY ({total_valuable} món):")
        for gl in grouped_lines:
            log(gl)
    else:
        log(f"\n🎁 (Hôm nay toàn bộ trúng vật phẩm thường, không có vật phẩm quý)")

    log(f"{'=' * 80}\n")
    return results
