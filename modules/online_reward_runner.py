"""
PVTK Online Reward & AFK Keep-Alive Module
Handles:
1. Multi-character auto-cycling: When a character finishes all online rewards, automatically switches to the next character until all characters on the account are complete.
2. Multi-account concurrency: Runs all selected accounts concurrently (via ThreadPoolExecutor) keeping them online without disconnecting.
3. Auto-claiming Online Rewards (Opcode 0x2B65 - CG_ACTOR_LOGIN_ONLINE_REWARD_CLAIM) at each milestone.
4. Real-time logging of rewards, quantities, and next milestone countdowns.
5. Auto-reconnecting on disconnect/network drops.
"""

import sys
import time
import struct
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple, Dict, Any, Set

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from core.device_faker import DeviceFaker
from core.message import Message
from core.network import PVTKConnection
from modules.auth import AuthManager, CharacterInfo
from modules.daily_history import DailyHistoryManager

VN_TZ = timezone(timedelta(hours=7))

OPCODE_ONLINE_REWARD_INFO = 0x2B64   # Server push: CG_ACTOR_LOGIN_ONLINE_REWARD
OPCODE_ONLINE_REWARD_CLAIM = 0x2B65  # Client claim & Server response: CG_ACTOR_LOGIN_ONLINE_REWARD_CLAIM
OPCODE_SYS_HEARTBEAT = 0x3E81        # System Heartbeat


def format_countdown(ms: int) -> str:
    """Formats milliseconds into 'X giờ Y phút Z s' matching in-game text."""
    total_sec = max(0, ms // 1000)
    hour = total_sec // 3600
    minute = (total_sec % 3600) // 60
    second = total_sec % 60
    parts = []
    if hour > 0:
        parts.append(f"{hour} giờ")
    if minute > 0:
        parts.append(f"{minute} phút")
    if second > 0 or not parts:
        parts.append(f"{second} s")
    return " ".join(parts)


def extract_reward_items(data: bytes) -> List[str]:
    """
    Extracts UTF-16 BE item names from packet payload.
    In PVTK messages, strings have a 3-byte prefix (0x00, 0x00, length) followed by length chars.
    """
    items = []
    for i in range(len(data) - 3):
        if data[i] == 0 and data[i+1] == 0 and 2 <= data[i+2] <= 80:
            length = data[i+2]
            str_bytes = data[i+3 : i+3 + length*2]
            if len(str_bytes) == length * 2:
                try:
                    s = str_bytes.decode("utf-16-be")
                    if s.isprintable() and len(s) >= 2 and not s.startswith("http"):
                        items.append(s)
                except Exception:
                    pass
    return items




from modules.base_runner import CharacterLoopRunner

class OnlineRewardRunner(CharacterLoopRunner):
    """Manages an active in-game AFK session for a single account across all characters."""

    _session_file_lock = threading.Lock()
    _session_cache: Dict[str, Tuple[int, int]] = {}

    @classmethod
    def _save_latest_session_id(cls, username: str, session_id: int, ukey: int):
        import os
        try:
            with cls._session_file_lock:
                if not cls._session_cache and os.path.exists("sessionIDs.txt"):
                    try:
                        with open("sessionIDs.txt", "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                parts = line.strip().split("|")
                                if len(parts) >= 3 and parts[1].lstrip("-").isdigit():
                                    cls._session_cache[parts[0]] = (int(parts[1]), int(parts[2]) if parts[2].lstrip("-").isdigit() else 0)
                    except Exception:
                        pass
                prev = cls._session_cache.get(username)
                if prev == (session_id, ukey):
                    return
                cls._session_cache[username] = (session_id, ukey)
                tmp_s = f"sessionIDs.txt.{os.getpid()}.tmp"
                with open(tmp_s, "w", encoding="utf-8") as f:
                    for u, (sid, uk) in cls._session_cache.items():
                        f.write(f"{u}|{sid}|{uk}\n")
                os.replace(tmp_s, "sessionIDs.txt")
        except Exception:
            pass

    def __init__(
        self,
        accounts: List[Tuple],
        auto_cycle_characters: bool = True,
        slot_choice: int = 1,
        stop_event: Optional[threading.Event] = None,
        thread_name: str = "Tài khoản",
        max_retries: int = 10,
        max_workers: Optional[int] = None
    ):
        super().__init__(accounts=accounts, thread_name=thread_name, max_retries=max_retries, max_workers=max_workers)
        self.auto_cycle_characters = auto_cycle_characters
        self.slot_choice = slot_choice
        self.stop_event = stop_event or threading.Event()
        self.char_rewards_claimed_global: Dict[str, Dict[int, int]] = {}
        self.char_status_summary_global: Dict[str, Dict[str, str]] = {}

        # Tracking cho Rút Thưởng & Vấn Đáp trực tiếp ngay trên phiên online
        self.completed_lottery_keys: Set[str] = set()
        self.completed_quiz_keys: Set[str] = set()
        self.lottery_requested: bool = False
        self.quiz_requested: bool = False
        self.lottery_results_summary: List[Dict[str, Any]] = []
        self.quiz_results_summary: List[Dict[str, Any]] = []

        # Tracking cho Dịch Chuyển trực tiếp ngay trên phiên online (không login lại)
        self.teleport_req_id: int = 0
        self.teleport_request: Optional[Dict[str, Any]] = None
        self.teleport_results_summary: List[Dict[str, Any]] = []
        self.action_lock = threading.Lock()

        # Tracking số tài khoản online thực tế theo thời gian thực (real-time socket connection)
        self.connected_accounts: Set[str] = set()
        self.connected_accounts_lock = threading.Lock()
        self.account_characters: Dict[str, List[CharacterInfo]] = {}
        self.account_characters_lock = threading.Lock()

        # Quản lý toàn bộ active connection sockets để đóng dứt điểm khi dừng/relogin
        self.active_connections: Dict[str, Any] = {}
        self.active_connections_lock = threading.Lock()

    @staticmethod
    def _safe_close_conn(conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def register_connection(self, username: str, conn):
        if conn is None:
            self.unregister_connection(username)
            return
        if self.stop_event.is_set():
            self._safe_close_conn(conn)
            return
        with self.active_connections_lock:
            prev = self.active_connections.get(username)
            self.active_connections[username] = conn
        if prev is not None and prev is not conn:
            self._safe_close_conn(prev)

    def unregister_connection(self, username: str):
        with self.active_connections_lock:
            self.active_connections.pop(username, None)

    def close_all_connections(self):
        with self.active_connections_lock:
            conns = list(self.active_connections.values())
            self.active_connections.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    def mark_account_connected(self, username: str):
        with self.connected_accounts_lock:
            self.connected_accounts.add(username)

    def mark_account_disconnected(self, username: str):
        with self.connected_accounts_lock:
            self.connected_accounts.discard(username)
        self.unregister_connection(username)

    def get_realtime_online_count(self) -> int:
        with self.connected_accounts_lock:
            return len(self.connected_accounts)

    def get_realtime_online_accounts(self) -> Set[str]:
        with self.connected_accounts_lock:
            return set(self.connected_accounts)

    def set_account_characters(self, username: str, characters: List[CharacterInfo]):
        with self.account_characters_lock:
            self.account_characters[username] = characters

    def get_account_characters(self, username: str) -> List[CharacterInfo]:
        with self.account_characters_lock:
            return list(self.account_characters.get(username, []))

    def is_in_session_quiz_done(self) -> bool:
        """Kiểm tra xem tất cả các tài khoản đang kết nối online đã làm xong vấn đáp hôm nay chưa."""
        with self.connected_accounts_lock:
            online_users = list(self.connected_accounts)
        if not online_users:
            return True
        for u in online_users:
            chars = self.get_account_characters(u)
            if chars:
                for c in chars:
                    if not DailyHistoryManager.is_quiz_done_today(u, c.name):
                        return False
        return True

    def is_in_session_lottery_done(self) -> bool:
        """Kiểm tra xem tất cả các tài khoản đang kết nối online đã rút thưởng hôm nay chưa."""
        with self.connected_accounts_lock:
            online_users = list(self.connected_accounts)
        if not online_users:
            return True
        for u in online_users:
            chars = self.get_account_characters(u)
            if chars:
                for c in chars:
                    if getattr(c, "level", 0) >= 30 and not DailyHistoryManager.is_lottery_done_today(u, c.name):
                        return False
        return True

    def auto_sell_all_herbs(self, conn, char_tag: str) -> int:
        sold_total = 0
        try:
            from modules.exp_ticket_sender import ExpTicketSender
            items = ExpTicketSender.get_bag_items(conn)
            if not items:
                return 0
                
            for it in items:
                if "Dược Thảo Hoạt Lực" in it.name or "Dược Thảo" in it.name:
                    qty = it.quantity
                    if qty <= 0:
                        continue
                        
                    sell_msg = Message(0x2F05)
                    sell_msg.putByte(1) # Number of items
                    sell_msg.putInt(it.id)
                    sell_msg.putShort(it.slotPos)
                    sell_msg.putShort(qty)
                    
                    res = conn.send_and_wait(sell_msg, expected_type=0x2F05, timeout=2.0)
                    if res and res.type == 0x2F05:
                        code = res.getByte()
                        if code >= 0:
                            sold_total += qty
                            self.log(f"{char_tag} - Đã bán {qty} x {it.name}")
                            
            return sold_total
        except Exception as e:
            return sold_total

    def trigger_in_session_lottery(self):
        with self.action_lock:
            self.lottery_requested = True
            self.lottery_results_summary.clear()

    def trigger_in_session_quiz(self):
        with self.action_lock:
            self.quiz_requested = True
            self.quiz_results_summary.clear()

    def trigger_in_session_teleport(self, target: str, map_name: str, map_id: int, min_level: int = 0, target_id: Optional[int] = None) -> int:
        with self.action_lock:
            self.teleport_req_id += 1
            self.teleport_request = {
                "id": self.teleport_req_id,
                "target": target,
                "map_name": map_name,
                "map_id": map_id,
                "min_level": min_level,
                "target_id": target_id or map_id
            }
            self.teleport_results_summary.clear()
            return self.teleport_req_id

    def get_teleport_summary(self) -> Dict[str, Any]:
        with self.action_lock:
            items = list(self.teleport_results_summary)
        total = len(items)
        ok = sum(1 for x in items if x.get("status") in ("OK", "ALREADY"))
        already = sum(1 for x in items if x.get("status") == "ALREADY")
        skipped = sum(1 for x in items if x.get("status") == "SKIPPED")
        failed = sum(1 for x in items if x.get("status") == "FAILED")
        return {
            "total": total,
            "ok": ok,
            "already": already,
            "skipped": skipped,
            "failed": failed,
            "details": items
        }

    def _handle_in_session_teleport(self, conn: PVTKConnection, char: CharacterInfo, username: str, char_tag: str, req: Dict[str, Any]):
        target = req.get("target", "")
        map_name = req.get("map_name", "")
        map_id = req.get("map_id", 0)
        min_level = req.get("min_level", 0)
        char_lv = getattr(char, "level", 0)

        from modules.teleport import MapTeleporter

        if min_level > 0 and char_lv < min_level:
            self.log(f"{char_tag} \033[93m[DỊCH CHUYỂN BỎ QUA]\033[0m Cấp độ {char_lv} chưa đủ điều kiện map {map_name} (yêu cầu Lv > 15 cho Tân Nguyệt hoặc Lv >= 35 cho Thánh Vực).")
            with self.action_lock:
                self.teleport_results_summary.append({
                    "username": username, "char_name": char.name, "level": char_lv,
                    "status": "SKIPPED", "reason": f"Lv {char_lv} < {min_level}"
                })
            return

        if target == "thoat_lag":
            self.log(f"{char_tag} 🔄 [THOÁT LAG TRỰC TIẾP] Đang gửi lệnh thoát lag (Cài đặt / H.Thống)...")
            ok, new_map, gx, gy, err = MapTeleporter.escape_lag(conn)
            err_lower = (err or "").lower()
            if ok:
                st = "OK"
                self.log(f"{char_tag} 🔄 [THOÁT LAG XONG] Thành công! Vẫn giữ nguyên phiên online.")
            elif any(kw in err_lower for kw in ["bản đồ này rồi", "ban do nay", "hồi sinh", "hoi sinh", "bản đồ này", "đã ở", "da o"]):
                st = "ALREADY"
                self.log(f"{char_tag} 🔄 [THOÁT LAG] Đã ở sẵn map hồi sinh (Tân Nguyệt Trấn)! Vẫn giữ nguyên phiên online.")
            else:
                st = "FAILED"
                self.log(f"{char_tag} ⚠️ [THOÁT LAG THẤT BẠI] {err}")

            with self.action_lock:
                self.teleport_results_summary.append({
                    "username": username, "char_name": char.name, "level": char_lv,
                    "status": st, "reason": err or f"Map {new_map}"
                })
        elif target == "city":
            target_id = req.get("target_id") or map_id
            self.log(f"{char_tag} 🏰 [VÀO THÀNH TRỰC TIẾP] Đang chuyển vào Thành của ID {target_id}...")
            from modules.city_manager import CityManager
            ok, err = CityManager.enter_city(conn, target_id)
            st = "OK" if ok else "FAILED"
            with self.action_lock:
                self.teleport_results_summary.append({
                    "username": username, "char_name": char.name, "level": char_lv,
                    "status": st, "reason": "" if ok else err
                })
            if ok:
                self.log(f"{char_tag} 🏰 [VÀO THÀNH XONG] Đã vào Thành của ID {target_id}! Tiếp tục giữ nguyên phiên online.")
            else:
                self.log(f"{char_tag} ⚠️ [VÀO THÀNH THẤT BẠI] {err}")
        else:
            self.log(f"{char_tag} 🚀 [DỊCH CHUYỂN TRỰC TIẾP] Đang chuyển sang {map_name} (Map ID: {map_id})...")
            ok = MapTeleporter.teleport_to_map(conn, map_id)
            st = "OK" if ok else "FAILED"
            with self.action_lock:
                self.teleport_results_summary.append({
                    "username": username, "char_name": char.name, "level": char_lv,
                    "status": st, "reason": "" if ok else "Lỗi gửi gói tin"
                })
            if ok:
                self.log(f"{char_tag} 🚀 [DỊCH CHUYỂN XONG] Đã đến {map_name}! Tiếp tục giữ nguyên phiên online.")

    def is_lottery_due(self, char: CharacterInfo, username: str = "") -> bool:
        if not char or getattr(char, "level", 0) < 30:
            return False
        # Kiểm tra lịch sử hoàn thành hôm nay để tránh lặp đi lặp lại
        if DailyHistoryManager.is_lottery_done_today(username, char.name):
            return False
        with self.action_lock:
            return bool(self.lottery_requested)

    def is_quiz_due(self, char: CharacterInfo, username: str = "") -> bool:
        if not char:
            return False
        # Kiểm tra lịch sử hoàn thành hôm nay để tránh lặp đi lặp lại
        if DailyHistoryManager.is_quiz_done_today(username, char.name):
            return False
        with self.action_lock:
            return bool(self.quiz_requested)

    def is_any_lottery_due(self, all_chars: List[CharacterInfo], username: str = "") -> bool:
        with self.action_lock:
            if not self.lottery_requested:
                return False
        for c in (all_chars or []):
            if getattr(c, "level", 0) >= 30 and not DailyHistoryManager.is_lottery_done_today(username, c.name):
                return True
        return False

    def is_any_quiz_due(self, all_chars: List[CharacterInfo], username: str = "") -> bool:
        with self.action_lock:
            if not self.quiz_requested:
                return False
        for c in (all_chars or []):
            if not DailyHistoryManager.is_quiz_done_today(username, c.name):
                return True
        return False

    def execute_in_session_lottery(self, conn: PVTKConnection, char: CharacterInfo, username: str, char_tag: str):
        if not char or getattr(char, "level", 0) < 30:
            return
        if DailyHistoryManager.is_lottery_done_today(username, char.name):
            rec = DailyHistoryManager.get_lottery_record(username, char.name)
            best_num = rec.get("best_number", 0) if rec else 0
            self.log(f"{char_tag} \033[90m[BỎ QUA RÚT THƯỞNG]\033[0m Đã hoàn thành hôm nay (Điểm: {best_num}). Tránh lặp lại.")
            return

        self.log(f"{char_tag} 🎲 [RÚT THƯỞNG TRỰC TIẾP] Đang dịch chuyển đến Quảng Trường Tân Nguyệt quay số may mắn...")
        try:
            from modules.teleport import MapTeleporter
            from modules.lottery_runner import LotteryManager
            MapTeleporter.teleport_to_quang_truong(conn)
            time.sleep(0.8)
            while conn.receive_message(timeout=0.05):
                pass
            res = LotteryManager.execute_lottery_for_character(conn, char_tag=char_tag, delay_sec=0.25)
            best_num = res.get("best_number", 0) if res else 0
            hit_target = res.get("hit_target", False) if res else False
            hit_max = res.get("hit_max_1000", False) if res else False
            rolls_done = res.get("rolls_done", 0) if res else 0
            status = res.get("status", "COMPLETED") if res else "COMPLETED"
            msg = res.get("message", "") if res else ""

            # Lưu vào lịch sử vĩnh viễn hôm nay tránh lặp lại
            DailyHistoryManager.save_lottery_done(
                username=username,
                char_name=char.name,
                level=char.level,
                best_number=best_num,
                rolls_done=rolls_done,
                hit_target=hit_target,
                hit_max_1000=hit_max,
                status=status,
                message=msg
            )

            with self.action_lock:
                self.lottery_results_summary.append({
                    "name": char.name,
                    "level": char.level,
                    "best_number": best_num,
                    "hit_target": hit_target,
                    "hit_max": hit_max
                })
            self.log(f"{char_tag} 🎲 [RÚT THƯỞNG XONG] Điểm cao nhất: \033[93m{best_num}\033[0m. Đã lưu lịch sử hôm nay. Tiếp tục online!")
            if hasattr(char, "map_name"):
                char.map_name = "Bản đồ hồi sinh"
        except Exception as e:
            self.log(f"{char_tag} [LỖI RÚT THƯỞNG TRỰC TIẾP] {e}")

    def execute_in_session_quiz(self, conn: PVTKConnection, char: CharacterInfo, username: str, char_tag: str):
        if not char or not conn or not getattr(conn, "is_connected", False):
            return
        if DailyHistoryManager.is_quiz_done_today(username, char.name):
            self.log(f"{char_tag} \033[90m[BỎ QUA VẤN ĐÁP]\033[0m Đã hoàn thành hôm nay. Tránh lặp lại.")
            return

        self.log(f"{char_tag} 🎓 [VẤN ĐÁP TRỰC TIẾP] Đang giải 20 câu hỏi Vấn Đáp tại vị trí hiện tại...")
        try:
            from modules.quiz_solver import QuizManager
            while conn.receive_message(timeout=0.05):
                pass
            is_done, q_cnt, c_cnt, msg = QuizManager.solve_character_quiz(conn, char_tag=char_tag, delay_sec=0.05)

            # Lưu vào lịch sử vĩnh viễn hôm nay tránh lặp lại
            DailyHistoryManager.save_quiz_done(
                username=username,
                char_name=char.name,
                level=char.level,
                questions=q_cnt,
                correct=c_cnt,
                is_done=is_done,
                message=msg
            )

            with self.action_lock:
                self.quiz_results_summary.append({
                    "name": char.name,
                    "level": char.level,
                    "questions": q_cnt,
                    "correct": c_cnt,
                    "is_done": is_done
                })
            self.log(f"{char_tag} 🎓 [VẤN ĐÁP XONG] Đúng \033[92m{c_cnt}/{q_cnt}\033[0m câu. Đã lưu lịch sử hôm nay. Tiếp tục online!")
        except Exception as e:
            self.log(f"{char_tag} [LỖI VẤN ĐÁP TRỰC TIẾP] {e}")

    def execute_account_lottery_rotation(
        self,
        active_conn: PVTKConnection,
        current_char: CharacterInfo,
        all_chars: List[CharacterInfo],
        login_setting: int,
        dev: Dict[str, str],
        username: str,
        password: str,
        proxy: Optional[str],
        thread_tag: str
    ) -> Tuple[PVTKConnection, CharacterInfo, int]:
        """
        Luân phiên toàn bộ 3 nhân vật trong 1 tài khoản để dịch chuyển đến Quảng Trường Tân Nguyệt rút may mắn.
        Sau khi xong: kiểm tra nhân vật cuối cùng xem còn mốc quà online không,
        nếu không thì đổi sang nhân vật khác còn mốc quà để treo tiếp.
        """
        curr = current_char
        conn = active_conn
        lsetting = login_setting

        self.log(f"{thread_tag} 🎲 [RÚT THƯỞNG LUÂN PHIÊN] Bắt đầu duyệt toàn bộ {len(all_chars)} nhân vật trên tài khoản...")

        for char in all_chars:
            if self.stop_event.is_set():
                break

            char_tag = f"{thread_tag} [{char.name}]"

            if getattr(char, "level", 0) < 30:
                self.log(f"{char_tag} \033[90m[BỎ QUA RÚT THƯỞNG]\033[0m Cấp độ {char.level} < 30.")
                continue

            if DailyHistoryManager.is_lottery_done_today(username, char.name):
                rec = DailyHistoryManager.get_lottery_record(username, char.name)
                best_num = rec.get("best_number", 0) if rec else 0
                self.log(f"{char_tag} \033[90m[BỎ QUA RÚT THƯỞNG]\033[0m Đã hoàn thành hôm nay (Điểm: {best_num}).")
                continue

            if curr.id != char.id:
                self.log(f"{thread_tag} 🔄 [ĐỔI NV RÚT THƯỞNG] Chuyển từ '{curr.name}' sang '{char.name}' (Lv.{char.level})...")
                try:
                    AuthManager.leave_character(conn)
                except Exception:
                    pass
                time.sleep(0.5)

                enter_ok = False
                try:
                    enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                except Exception:
                    enter_ok = False

                if not enter_ok:
                    self._safe_close_conn(conn)
                    conn = None
                    new_conn, new_login = AuthManager.reconnect_game_session(
                        settings.game_host, settings.game_port,
                        settings.master_host, settings.master_port,
                        username, password, proxy=proxy
                    )
                    if new_conn:
                        conn = new_conn
                        self.register_connection(username, conn)
                        lsetting = new_login
                        try:
                            enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                        except Exception:
                            enter_ok = False

                if not enter_ok:
                    self.log(f"{char_tag} \033[91m[LỖI ĐỔI NV]\033[0m Không vào được nhân vật {char.name}.")
                    continue

                curr = char

            self.execute_in_session_lottery(conn, curr, username, f"{thread_tag} [{curr.name}]")
            time.sleep(0.5)

        # Kiểm tra mốc quà online của nhân vật hiện tại
        self.log(f"{thread_tag} 🔍 [KIỂM TRA MỐC TREO QUÀ] Kiểm tra mốc quà online nhân vật '{curr.name}'...")
        st, items, countdown_sec, all_done = self.claim_reward(conn, curr, username, proxy, f"{thread_tag} [{curr.name}]")

        if not all_done:
            self.log(f"{thread_tag} 🟢 [GIỮ TREO NHÂN VẬT] '{curr.name}' còn mốc quà online! Tiếp tục giữ treo.")
            return conn, curr, lsetting

        # Nếu nhân vật hiện tại đã hết quà -> tìm nhân vật khác còn mốc quà để đổi sang treo
        self.log(f"{thread_tag} ℹ️ Nhân vật '{curr.name}' đã nhận hết quà online. Đang tìm nhân vật khác còn quà...")
        for char in all_chars:
            if char.id == curr.id:
                continue

            self.log(f"{thread_tag} 🔄 [ĐỔI NV TREO QUÀ] Kiểm tra nhân vật '{char.name}' (Lv.{char.level})...")
            try:
                AuthManager.leave_character(conn)
            except Exception:
                pass
            time.sleep(0.5)

            enter_ok = False
            try:
                enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
            except Exception:
                enter_ok = False

            if not enter_ok:
                self._safe_close_conn(conn)
                conn = None
                new_conn, new_login = AuthManager.reconnect_game_session(
                    settings.game_host, settings.game_port,
                    settings.master_host, settings.master_port,
                    username, password, proxy=proxy
                )
                if new_conn:
                    conn = new_conn
                    self.register_connection(username, conn)
                    lsetting = new_login
                    try:
                        enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                    except Exception:
                        enter_ok = False

            if not enter_ok:
                continue

            curr = char
            st_c, it_c, cd_c, all_done_c = self.claim_reward(conn, curr, username, proxy, f"{thread_tag} [{curr.name}]")
            if not all_done_c:
                self.log(f"{thread_tag} 🟢 [CHỌN ĐƯỢC NV TREO QUÀ] Nhân vật '{curr.name}' còn mốc quà online! Bắt đầu treo.")
                return conn, curr, lsetting
            else:
                self.log(f"{thread_tag} ℹ️ Nhân vật '{curr.name}' cũng đã nhận hết quà.")

        self.log(f"{thread_tag} 🎉 [TẤT CẢ NV ĐÃ HẾT QUÀ] Tất cả nhân vật trên acc {username} đều đã nhận đủ quà hôm nay!")
        return conn, curr, lsetting

    def execute_account_quiz_rotation(
        self,
        active_conn: PVTKConnection,
        current_char: CharacterInfo,
        all_chars: List[CharacterInfo],
        login_setting: int,
        dev: Dict[str, str],
        username: str,
        password: str,
        proxy: Optional[str],
        thread_tag: str
    ) -> Tuple[PVTKConnection, CharacterInfo, int]:
        """
        Luân phiên toàn bộ 3 nhân vật trong 1 tài khoản để trả lời 20 câu hỏi Vấn Đáp trực tiếp tại chỗ (không dịch chuyển map).
        Sau khi xong: kiểm tra và chọn nhân vật còn mốc quà online để tiếp tục treo.
        """
        curr = current_char
        conn = active_conn
        lsetting = login_setting

        self.log(f"{thread_tag} 🎓 [VẤN ĐÁP LUÂN PHIÊN] Bắt đầu duyệt toàn bộ {len(all_chars)} nhân vật trên tài khoản...")

        for char in all_chars:
            if self.stop_event.is_set():
                break

            char_tag = f"{thread_tag} [{char.name}]"

            if DailyHistoryManager.is_quiz_done_today(username, char.name):
                self.log(f"{char_tag} \033[90m[BỎ QUA VẤN ĐÁP]\033[0m Đã hoàn thành hôm nay.")
                continue

            if curr.id != char.id:
                self.log(f"{thread_tag} 🔄 [ĐỔI NV VẤN ĐÁP] Chuyển từ '{curr.name}' sang '{char.name}' (Lv.{char.level})...")
                try:
                    AuthManager.leave_character(conn)
                except Exception:
                    pass
                time.sleep(0.5)

                enter_ok = False
                try:
                    enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                except Exception:
                    enter_ok = False

                if not enter_ok:
                    self._safe_close_conn(conn)
                    conn = None
                    new_conn, new_login = AuthManager.reconnect_game_session(
                        settings.game_host, settings.game_port,
                        settings.master_host, settings.master_port,
                        username, password, proxy=proxy
                    )
                    if new_conn:
                        conn = new_conn
                        self.register_connection(username, conn)
                        lsetting = new_login
                        try:
                            enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                        except Exception:
                            enter_ok = False

                if not enter_ok:
                    self.log(f"{char_tag} \033[91m[LỖI ĐỔI NV]\033[0m Không vào được nhân vật {char.name}.")
                    continue

                curr = char

            self.execute_in_session_quiz(conn, curr, username, f"{thread_tag} [{curr.name}]")
            time.sleep(0.5)

        # Sau khi vấn đáp xong, kiểm tra nhân vật treo quà
        self.log(f"{thread_tag} 🔍 [KIỂM TRA MỐC TREO QUÀ] Kiểm tra mốc quà online nhân vật '{curr.name}'...")
        st, items, countdown_sec, all_done = self.claim_reward(conn, curr, username, proxy, f"{thread_tag} [{curr.name}]")

        if not all_done:
            self.log(f"{thread_tag} 🟢 [GIỮ TREO NHÂN VẬT] '{curr.name}' còn mốc quà online! Tiếp tục giữ treo.")
            return conn, curr, lsetting

        for char in all_chars:
            if char.id == curr.id:
                continue

            try:
                AuthManager.leave_character(conn)
            except Exception:
                pass
            time.sleep(0.5)

            enter_ok = False
            try:
                enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
            except Exception:
                enter_ok = False

            if not enter_ok:
                self._safe_close_conn(conn)
                conn = None
                new_conn, new_login = AuthManager.reconnect_game_session(
                    settings.game_host, settings.game_port,
                    settings.master_host, settings.master_port,
                    username, password, proxy=proxy
                )
                if new_conn:
                    conn = new_conn
                    self.register_connection(username, conn)
                    lsetting = new_login
                    try:
                        enter_ok = AuthManager.enter_character(conn, char.id, lsetting, device_id=dev["android_id"], char_info=char)
                    except Exception:
                        enter_ok = False

            if not enter_ok:
                continue

            curr = char
            st_c, it_c, cd_c, all_done_c = self.claim_reward(conn, curr, username, proxy, f"{thread_tag} [{curr.name}]")
            if not all_done_c:
                self.log(f"{thread_tag} 🟢 [CHỌN ĐƯỢC NV TREO QUÀ] Nhân vật '{curr.name}' còn mốc quà online! Bắt đầu treo.")
                return conn, curr, lsetting

        return conn, curr, lsetting

    def get_lottery_summary_message(self) -> str:
        # Lấy thống kê tổng quát và chi tiết từ DailyHistoryManager
        msg = DailyHistoryManager.format_lottery_summary_message(
            title="🎲 <b>BÁO CÁO RÚT THƯỞNG MAY MẮN (TRỰC TIẾP TRONG PHIÊN ONLINE)</b>"
        )
        return (
            f"{msg}\n"
            "🟢 <i>Tất cả tài khoản vẫn giữ kết nối online 100%, không hề bị ngắt!</i>"
        )

    def get_quiz_summary_message(self) -> str:
        summary = DailyHistoryManager.get_today_quiz_summary()
        total_ran = summary.get("total_chars", 0)
        total_correct = summary.get("total_correct", 0)
        total_done = summary.get("total_done", 0)

        return (
            "🎓 <b>BÁO CÁO VẤN ĐÁP (TRỰC TIẾP TRONG PHIÊN ONLINE)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧙‍♂️ <b>Nhân vật hoàn thành:</b> {total_done}/{total_ran}\n"
            f"🎯 <b>Tổng câu trả lời đúng:</b> {total_correct:,} câu\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💾 <i>Lịch sử đã được lưu vào storage/quiz_history.json tránh lặp lại!</i>\n"
            "🟢 <i>Tất cả tài khoản vẫn giữ kết nối online 100%, không hề bị ngắt!</i>"
        )


    def claim_reward(self, game_conn, current_char, username, proxy, char_tag: str) -> Tuple[int, List[str], int, bool]:
        """
        Sends Opcode 0x2B65 to claim or check next online reward milestone.
        Returns: (status_code, item_names, countdown_seconds, all_done)
        """
        if not game_conn or not game_conn.is_connected:
            return -1, [], 30, False

        try:
            msg = Message(OPCODE_ONLINE_REWARD_CLAIM)
            if not game_conn.send_message(msg):
                return -1, [], 30, False

            res = game_conn.receive_message(timeout=3.0)
            if not res or res.type != OPCODE_ONLINE_REWARD_CLAIM:
                return -1, [], 30, False

            rs = res.getByte()

            if rs == 0:
                # Milestone reached! Claimed successfully!
                if proxy:
                    from modules.free_proxy import FreeProxyManager
                    FreeProxyManager.mark_proxy_good(proxy)
                    
                items = extract_reward_items(res.data)
                items_desc = f"\033[92m{items[0]}\033[0m" if items else "Vật phẩm sự kiện"
                cid = current_char.id if current_char else 0
                
                if username not in self.char_rewards_claimed_global:
                    self.char_rewards_claimed_global[username] = {}
                self.char_rewards_claimed_global[username][cid] = self.char_rewards_claimed_global[username].get(cid, 0) + 1
                
                if current_char:
                    if username not in self.char_status_summary_global:
                        self.char_status_summary_global[username] = {}
                    self.char_status_summary_global[username][current_char.name] = f"Đang treo ({self.char_rewards_claimed_global[username][cid]} mốc)"

                self.log(f"{char_tag} 🎁 \033[92m[NHẬN QUÀ THÀNH CÔNG]\033[0m Vừa nhận: {items_desc} | Đang chờ thông tin mốc tiếp theo...")
                return 0, items, 5, False

            elif rs == 1:
                # Still counting down
                if proxy:
                    from modules.free_proxy import FreeProxyManager
                    FreeProxyManager.mark_proxy_good(proxy)
                    
                next_ms = res.getLong() if res.hasRemaining() else 0
                if next_ms > 0:
                    items = extract_reward_items(res.data)
                    items_desc = f"\033[96m{items[0]}\033[0m" if items else "Quà ẩn"
                    self.log(f"{char_tag} ⏳ [ĐANG CHỜ MỐC] Phần thưởng tiếp theo: {items_desc} | Còn \033[93m{format_countdown(next_ms)}\033[0m nữa.")
                    return 1, [], max(1, next_ms // 1000), False
                else:
                    # Countdown <= 0 and rs == 1 means all rewards finished!
                    if current_char:
                        claimed_count = self.char_rewards_claimed_global.get(username, {}).get(current_char.id, 0)
                        DailyHistoryManager.save_online_reward_done(username, current_char.name, claimed_count)
                    return 1, [], 0, True

            elif rs <= 0:
                err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi {rs}"
                err_lower = err_msg.lower()
                if any(kw in err_lower for kw in ["hết", "het", "hôm nay", "hom nay", "đã nhận", "da nhan", "chưa mở", "khong the", "không thể", "không có", "khong co", "f5", "đủ điều kiện"]):
                    self.log(f"{char_tag} \033[93m[THÔNG BÁO]\033[0m {err_msg} -> Đã nhận hết mốc quà hôm nay.")
                    if current_char:
                        claimed_count = self.char_rewards_claimed_global.get(username, {}).get(current_char.id, 0)
                        DailyHistoryManager.save_online_reward_done(username, current_char.name, claimed_count)
                    return -1, [], 0, True
                elif "đầy" in err_lower or "day" in err_lower or "đủ chỗ" in err_lower or "du cho" in err_lower:
                    self.log(f"{char_tag} \033[93m[THÔNG BÁO]\033[0m {err_msg} -> Tự động dọn rác (Bán Dược Thảo Hoạt Lực)...")
                    sold_count = self.auto_sell_all_herbs(game_conn, char_tag)
                    if sold_count > 0:
                        self.log(f"{char_tag} ♻️ \033[92m[DỌN RÁC THÀNH CÔNG]\033[0m Đã bán {sold_count} món đồ rác để làm trống túi.")
                        return -1, [], 2, False # Chờ 2s rồi thử nhận lại
                    else:
                        self.log(f"{char_tag} ⚠️ \033[91m[DỌN RÁC THẤT BẠI]\033[0m Không có Dược Thảo Hoạt Lực nào để bán, bạn phải tự dọn tay!")
                        return -1, [], 30, False
                else:
                    self.log(f"{char_tag} \033[93m[CHƯA NHẬN ĐƯỢC]\033[0m {err_msg}")
                    return -1, [], 30, False

        except Exception as e:
            self.log(f"{char_tag} [LỖI XỬ LÝ QUÀ] {e}")

        return -1, [], 30, False

    def afk_character_loop(
        self,
        game_conn,
        char: CharacterInfo,
        login_setting,
        dev,
        username,
        password,
        proxy,
        char_tag: str,
        all_chars: Optional[List[CharacterInfo]] = None,
        thread_tag: str = "",
        standby_mode: bool = False,
        active_date: str = ""
    ) -> Tuple[Any, PVTKConnection, CharacterInfo]:
        """
        Keeps character online, claims rewards when ready until finished for today.
        If standby_mode is True, keeps character connected in game 24/7 without disconnecting.
        Returns: (True/False/'DATE_CHANGED', active_game_conn, current_character)
        """
        last_heartbeat = 0.0
        next_claim_time = 0.0  # Check immediately on enter
        reconnect_attempts = 0
        last_event_check = 0.0
        last_processed_teleport_id = 0
        active_conn = game_conn
        chars_to_check = all_chars if all_chars else [char]

        # Tự động rút thưởng hoặc vấn đáp luân phiên 3 nhân vật nếu đến giờ và chưa hoàn thành hôm nay
        if self.is_any_lottery_due(chars_to_check, username):
            active_conn, char, login_setting = self.execute_account_lottery_rotation(
                active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
            )
            char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"

        if self.is_any_quiz_due(chars_to_check, username):
            active_conn, char, login_setting = self.execute_account_quiz_rotation(
                active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
            )
            char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"

        while not self.stop_event.is_set():
            # Check connection
            if not active_conn or not active_conn.is_connected:
                self.mark_account_disconnected(username)
                self._safe_close_conn(active_conn)
                active_conn = None
                if reconnect_attempts >= 10:
                    self.log(f"{char_tag} \033[91m[LỖI MẠNG]\033[0m Mất kết nối nhiều lần, chờ 10s rồi thử lại...")
                    if self.stop_event.wait(timeout=10.0):
                        break
                    reconnect_attempts = 0
                if self.stop_event.is_set():
                    break
                self.log(f"{char_tag} \033[93m[KẾT NỐI LẠI]\033[0m Mất kết nối, đang đăng nhập lại (Lần {reconnect_attempts + 1}/10)...")
                
                # Chủ động đổi proxy lập tức khi mất kết nối
                if proxy:
                    from modules.free_proxy import FreeProxyManager
                    FreeProxyManager.release_proxy(proxy)
                    FreeProxyManager.mark_proxy_dead(proxy)
                    new_proxy = FreeProxyManager.acquire_proxy(exclude_proxy=proxy)
                    if new_proxy and new_proxy != proxy:
                        self.log(f"{char_tag} \033[93m[ĐỔI PROXY THÀNH CÔNG]\033[0m Chuyển từ {proxy} sang {new_proxy}")
                        proxy = new_proxy
                        FreeProxyManager.save_proxy_for_account(username, password, proxy)
                        
                if self.stop_event.wait(timeout=1.0):
                    break
                new_conn, new_login = AuthManager.reconnect_game_session(
                    settings.game_host, settings.game_port,
                    settings.master_host, settings.master_port,
                    username, password, proxy=proxy
                )
                if self.stop_event.is_set():
                    self._safe_close_conn(new_conn)
                    break
                if new_conn:
                    active_conn = new_conn
                    login_setting = new_login
                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"], char_info=char)
                    except Exception:
                        enter_ok = False

                    if enter_ok:
                        self.mark_account_connected(username)
                        self.register_connection(username, active_conn)
                        reconnect_attempts = 0
                        next_claim_time = 0.0
                        if self.is_any_lottery_due(chars_to_check, username):
                            active_conn, char, login_setting = self.execute_account_lottery_rotation(
                                active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
                            )
                            char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"
                        if self.is_any_quiz_due(chars_to_check, username):
                            active_conn, char, login_setting = self.execute_account_quiz_rotation(
                                active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
                            )
                            char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"
                    else:
                        self._safe_close_conn(active_conn)
                        active_conn = None
                        reconnect_attempts += 1
                        continue
                else:
                    if proxy:
                        from modules.free_proxy import FreeProxyManager
                        FreeProxyManager.mark_proxy_dead(proxy)
                    reconnect_attempts += 1
                    continue

            # Listen for server push packets (non-blocking)
            try:
                res = active_conn.receive_message(timeout=0.8)
                if res:
                    if res.type == OPCODE_ONLINE_REWARD_INFO:
                        self.log(f"DEBUG 0x2B64 HEX: {res.data.hex().upper()}")
                        is_show = res.getBoolean() if res.hasRemaining() else True
                        if not is_show:
                            # Server says all rewards claimed for today!
                            DailyHistoryManager.save_online_reward_done(username, char.name, self.char_rewards_claimed_global.get(username, {}).get(char.id, 0))
                            return True, active_conn, char
                        else:
                            self.log(f"{char_tag} 🔔 [MỐC QUÀ SẴN SÀNG] Server thông báo mốc quà đã đạt! Đang nhận...")
                            _st, _it, _cd, all_done = self.claim_reward(active_conn, char, username, proxy, char_tag)
                            if all_done:
                                return True, active_conn, char
                    elif res.type == 0x0005:
                        self.log(f"{char_tag} \033[91m[BỊ NGẮT KẾT NỐI BỞI SERVER]\033[0m")
                        self.mark_account_disconnected(username)
                        active_conn.close()
                        continue
            except Exception:
                pass

            now = time.time()

            # Tự động phát hiện chuyển sang ngày mới (00:00 midnight)
            today_vn = datetime.now(VN_TZ).strftime("%Y-%m-%d")
            if active_date and today_vn != active_date:
                self.log(f"{char_tag} 🌅 [NGÀY MỚI {today_vn}] Phát hiện chuyển sang ngày mới! Bắt đầu lại chu trình nhận quà...")
                return "DATE_CHANGED", active_conn, char

            # Khi có lệnh trigger từ run_247 hoặc Telegram: Chạy Rút Thưởng / Vấn Đáp trực tiếp trên phiên online
            if self.lottery_requested:
                if self.is_any_lottery_due(chars_to_check, username):
                    active_conn, char, login_setting = self.execute_account_lottery_rotation(
                        active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
                    )
                    char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"
                last_event_check = now

            if self.quiz_requested:
                if self.is_any_quiz_due(chars_to_check, username):
                    active_conn, char, login_setting = self.execute_account_quiz_rotation(
                        active_conn, char, chars_to_check, login_setting, dev, username, password, proxy, thread_tag or char_tag
                    )
                    char_tag = f"{thread_tag} [{char.name}]" if thread_tag else f"[{char.name}]"
                last_event_check = now

            # Dịch chuyển trực tiếp trên phiên online đang mở nếu có yêu cầu
            if self.teleport_request and self.teleport_request.get("id") != last_processed_teleport_id:
                req = dict(self.teleport_request)
                last_processed_teleport_id = req.get("id", 0)
                self._handle_in_session_teleport(active_conn, char, username, char_tag, req)

            if now >= next_claim_time:
                _st, _it, countdown_sec, all_done = self.claim_reward(active_conn, char, username, proxy, char_tag)
                last_heartbeat = now
                if all_done:
                    return True, active_conn, char
                elif countdown_sec > 0:
                    next_claim_time = now + countdown_sec
                else:
                    next_claim_time = now + 25.0

            # Keepalive ping every 25s
            if now - last_heartbeat >= 25.0:
                try:
                    ping = Message(OPCODE_ONLINE_REWARD_CLAIM)
                    active_conn.send_message(ping)
                    last_heartbeat = now
                except Exception:
                    pass

            time.sleep(0.2)

        return False, active_conn, char

    def process_account(self, game_conn, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any]):
        username = task[2]
        password = task[3]
        proxy = task[4] if len(task) >= 5 else None

        active_conn = game_conn

        if username not in self.char_rewards_claimed_global:
            self.char_rewards_claimed_global[username] = {}
        if username not in self.char_status_summary_global:
            self.char_status_summary_global[username] = {}

        # Lưu session_id và ukey mới nhất của mỗi tài khoản vào file (giữ gọn file, tránh phình to)
        session_id = account_result.get("session_id")
        ukey = account_result.get("ukey", 0)
        if session_id is not None:
            self._save_latest_session_id(username, session_id, ukey)

        try:
            # Fetch characters
            characters = AuthManager.fetch_character_list(active_conn)
            if not characters:
                self.log(f"{thread_tag} \033[93m[CẢNH BÁO]\033[0m Tài khoản không có nhân vật.")
                account_result["completed"] = True
                account_result["error"] = "Tài khoản không có nhân vật."
                return

            self.set_account_characters(username, characters)

            # Choose list of characters
            if self.auto_cycle_characters:
                char_list = list(characters)
            else:
                if self.slot_choice == 2 and len(characters) >= 2:
                    char_list = [characters[1]]
                elif self.slot_choice == 3 and len(characters) >= 3:
                    char_list = [characters[2]]
                elif self.slot_choice == 4:
                    char_list = [max(characters, key=lambda c: c.level)]
                else:
                    char_list = [characters[0]]

            char_names = [c.name for c in char_list]
            if DailyHistoryManager.is_account_online_reward_done_today(username, char_names) or DailyHistoryManager.is_account_fully_done_today(username):
                DailyHistoryManager.save_account_fully_done_today(username)
                self.log(f"{thread_tag} 🏆 \033[92m[ĐÃ HOÀN TẤT HÔM NAY]\033[0m Toàn bộ nhân vật ({', '.join(char_names)}) đã nhận đủ quà online hôm nay! Ngắt kết nối (OFFLINE).")
                for c in char_list:
                    self.char_status_summary_global[username][c.name] = "Xong quà (Offline)"
                account_result["completed"] = True
                return

            finished_char_ids = set()
            for c in char_list:
                if DailyHistoryManager.is_online_reward_done_today(username, c.name):
                    finished_char_ids.add(c.id)
                    self.char_status_summary_global[username][c.name] = "Xong quà (Offline)"

            current_active_date = datetime.now(VN_TZ).strftime("%Y-%m-%d")

            while not self.stop_event.is_set():
                today_vn = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                if today_vn != current_active_date:
                    self.log(f"{thread_tag} 🌅 [NGÀY MỚI {today_vn}] Reset mốc nhận quà ngày mới cho toàn bộ nhân vật!")
                    finished_char_ids.clear()
                    current_active_date = today_vn

                # Tìm nhân vật kế tiếp trong char_list chưa nhận hết quà hôm nay
                next_char = None
                for c in char_list:
                    if c.id not in finished_char_ids and not DailyHistoryManager.is_online_reward_done_today(username, c.name):
                        next_char = c
                        break
                    else:
                        finished_char_ids.add(c.id)

                if next_char:
                    char = next_char
                    char_tag = f"{thread_tag} [{char.name}]"
                    self.log(f"{char_tag} 🎮 [BẮT ĐẦU TREO NHÂN VẬT] '{char.name}' (Lv.{char.level})")

                    enter_ok = False
                    try:
                        enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"], char_info=char)
                    except Exception:
                        pass
                        
                    if not enter_ok:
                        self.log(f"{char_tag} \033[93m[KẾT NỐI LẠI PHIÊN]\033[0m Kết nối cũ bị ngắt khi đổi NV, đang kết nối lại phiên game...")
                        self._safe_close_conn(active_conn)
                        active_conn = None
                        if self.stop_event.wait(timeout=1.0):
                            break
                        new_conn, new_login = AuthManager.reconnect_game_session(
                            settings.game_host, settings.game_port,
                            settings.master_host, settings.master_port,
                            username, password, proxy=proxy
                        )
                        if self.stop_event.is_set():
                            self._safe_close_conn(new_conn)
                            break
                        if new_conn:
                            active_conn = new_conn
                            self.register_connection(username, active_conn)
                            login_setting = new_login
                            try:
                                enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"], char_info=char)
                            except Exception:
                                enter_ok = False
                            if not enter_ok:
                                self._safe_close_conn(active_conn)
                                active_conn = None
                        if not new_conn or not enter_ok:
                            # Proxy có thể đã die, thử đổi proxy từ FreeProxyManager
                            if proxy:
                                try:
                                    from modules.free_proxy import FreeProxyManager
                                    curr_url = proxy.to_url() if hasattr(proxy, "to_url") else str(proxy)
                                    FreeProxyManager.release_proxy(curr_url)
                                    FreeProxyManager.mark_proxy_dead(curr_url)
                                    new_px = FreeProxyManager.acquire_proxy(exclude_proxy=curr_url)
                                    if new_px:
                                        proxy = new_px
                                        FreeProxyManager.save_proxy_for_account(username, password, proxy)
                                        self.log(f"{char_tag} 🔄 Đổi sang proxy mới ({proxy}) để kết nối lại...")
                                        new_conn, new_login = AuthManager.reconnect_game_session(
                                            settings.game_host, settings.game_port,
                                            settings.master_host, settings.master_port,
                                            username, password, proxy=proxy
                                        )
                                        if self.stop_event.is_set():
                                            self._safe_close_conn(new_conn)
                                            break
                                        if new_conn:
                                            active_conn = new_conn
                                            self.register_connection(username, active_conn)
                                            login_setting = new_login
                                            try:
                                                enter_ok = AuthManager.enter_character(active_conn, char.id, login_setting, device_id=dev["android_id"], char_info=char)
                                            except Exception:
                                                enter_ok = False
                                            if not enter_ok:
                                                self._safe_close_conn(active_conn)
                                                active_conn = None
                                except Exception as e_px:
                                    self.log(f"{char_tag} Lỗi xoay proxy: {e_px}")

                    if not enter_ok:
                        self._safe_close_conn(active_conn)
                        active_conn = None
                        if self.stop_event.is_set():
                            break
                        self.log(f"{char_tag} \033[91m[VÀO GAME THẤT BẠI]\033[0m Lỗi kết nối nhân vật '{char.name}', thử lại sau 3s...")
                        self.char_status_summary_global[username][char.name] = "Lỗi vào game (Thử lại)"
                        time.sleep(3.0)
                        continue

                    self.log(f"{char_tag} \033[92m[VÀO GAME THÀNH CÔNG]\033[0m Vị trí: \033[96m{char.map_name}\033[0m. Đang theo dõi nhận quà...")
                    self.mark_account_connected(username)
                    self.register_connection(username, active_conn)
                    
                    claimed_count = self.char_rewards_claimed_global[username].get(char.id, 0)
                    self.char_status_summary_global[username][char.name] = f"Đang treo ({claimed_count} mốc)"

                    # Chạy vòng lặp AFK cho nhân vật (chế độ nhận quà)
                    res_status, active_conn, last_active_char = self.afk_character_loop(
                        active_conn, char, login_setting, dev, username, password, proxy, char_tag,
                        all_chars=characters, thread_tag=thread_tag,
                        standby_mode=False, active_date=current_active_date
                    )
                    self.register_connection(username, active_conn)

                    if res_status == "DATE_CHANGED":
                        finished_char_ids.clear()
                        current_active_date = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                        try:
                            AuthManager.leave_character(active_conn)
                        except Exception:
                            pass
                        time.sleep(1.0)
                        continue
                    elif res_status is True:
                        claimed_count = self.char_rewards_claimed_global[username].get(last_active_char.id, 0)
                        finished_char_ids.add(last_active_char.id)
                        self.log(f"{thread_tag} 🏆 \033[92m[ĐÃ NHẬN HẾT QUÀ HÔM NAY]\033[0m Nhân vật '{last_active_char.name}' đã nhận đủ quà ({claimed_count} mốc)!")
                        self.char_status_summary_global[username][last_active_char.name] = f"Xong ({claimed_count} mốc)"
                        try:
                            AuthManager.leave_character(active_conn)
                        except Exception:
                            pass
                        time.sleep(1.0)
                        continue
                    else:
                        if self.stop_event.is_set():
                            break
                        time.sleep(2.0)
                        continue

                else:
                    # TOÀN BỘ NHÂN VẬT ĐÃ NHẬN HẾT QUÀ HÔM NAY -> TỰ NGẮT KẾT NỐI (OFFLINE)!
                    DailyHistoryManager.save_account_fully_done_today(username)
                    self.log(f"{thread_tag} 🏆 \033[92m[HOÀN TẤT TOÀN BỘ NHÂN VẬT]\033[0m Toàn bộ {len(char_list)} nhân vật của tài khoản '{username}' đã nhận đủ quà hôm nay. Tự động ngắt kết nối (OFFLINE)!")
                    for c in char_list:
                        self.char_status_summary_global[username][c.name] = "Xong quà (Offline)"
                    account_result["completed"] = True
                    break

            account_result["completed"] = not self.stop_event.is_set()
            account_result["error"] = "" if not self.stop_event.is_set() else "Dừng bởi người dùng"

        finally:
            self.unregister_connection(username)
            self.mark_account_disconnected(username)
            # Clean shutdown khi dừng luồng
            if active_conn:
                try:
                    AuthManager.leave_character(active_conn)
                except Exception:
                    pass
                try:
                    active_conn.close()
                except Exception:
                    pass


def run_all_online_reward(
    accounts: List[Tuple],
    auto_cycle_characters: bool = True,
    slot_choice: int = 1,
    max_workers: int = 8,
    stop_event: Optional[threading.Event] = None
):
    """
    Runs multi-threaded online keep-alive runner for all selected accounts.
    Supports auto-switching characters when each character finishes all rewards.
    """
    total = len(accounts)
    mode_text = "Tự động luân chuyển NV khi nhận hết quà" if auto_cycle_characters else f"Cố định Slot {slot_choice}"
    actual_workers = min(total, max_workers) if max_workers and max_workers > 0 else total

    print("\n" + "=" * 80)
    print(f"   BẮT ĐẦU TREO GAME ONLINE & NHẬN QUÀ TỰ ĐỘNG ({total} TÀI KHOẢN)")
    print(f"-> Chế độ: \033[92m{mode_text}\033[0m | Luồng đồng thời: \033[93m{actual_workers}\033[0m")
    print("-> Cơ chế: Giữ kết nối socket, định kỳ nhận quà, hết quà đổi NV, tự kết nối lại nếu rớt mạng.")
    print("-> \033[93m[LƯU Ý]\033[0m Nhấn \033[91mCtrl + C\033[0m bất kỳ lúc nào để DỪNG và thoát an toàn.")
    print("=" * 80 + "\n", flush=True)

    if stop_event is None:
        stop_event = threading.Event()

    # Xóa trắng file lưu session_id mỗi lần chạy theo yêu cầu
    try:
        with open("sessionIDs.txt", "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass

    account_tasks = [
        (
            i + 1, total, acc[0], acc[1],
            acc[2] if len(acc) >= 3 else None,
            acc[3] if len(acc) >= 8 else settings.game_host,
            acc[4] if len(acc) >= 8 else settings.game_port,
            acc[5] if len(acc) >= 8 else settings.master_host,
            acc[6] if len(acc) >= 8 else settings.master_port,
            acc[7] if len(acc) >= 8 else settings.game_server_name
        )
        for i, acc in enumerate(accounts)
    ]
    
    runner = OnlineRewardRunner(
        accounts=account_tasks,
        auto_cycle_characters=auto_cycle_characters,
        slot_choice=slot_choice,
        stop_event=stop_event,
        thread_name="Tài khoản",
        max_retries=10,
        max_workers=actual_workers
    )

    all_results = runner.run_all()

    # Process all_results to build a map of errors
    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}

    # Print Summary Table
    print("\n" + "=" * 85)
    print("                 TỔNG KẾT TIẾN TRÌNH TREO QUÀ ONLINE TẤT CẢ ACC")
    print("=" * 85)
    print(f"-> Tổng tài khoản đã treo: {total}")
    print(f"{'STT':<4} | {'TÊN TÀI KHOẢN':<16} | {'CHI TIẾT TRẠNG THÁI TỪNG NHÂN VẬT'}")
    print("-" * 85)
    
    for i, acc in enumerate(accounts):
        username = acc[0]
        res = results_by_user.get(username)
        
        # If the account had a fatal error (like login failed or proxy ban)
        if res and res.get("error"):
            summary_str = f"\033[91m[LỖI]\033[0m {res['error']}"
        elif not res:
            summary_str = "\033[91m[LỖI]\033[0m Chưa thể chạy (Hủy giữa chừng)"
        else:
            char_summary = runner.char_status_summary_global.get(username, {})
            if char_summary:
                summary_str = " | ".join([f"\033[96m{name}\033[0m: {st}" for name, st in char_summary.items()])
                if res.get("completed"):
                    summary_str += " \033[92m(XONG)\033[0m"
            else:
                summary_str = "Chưa vào được game / Lỗi mạng"
                
        print(f"  {i+1:<2} | {username:<16} | {summary_str}")
    print("=" * 85)

