"""
PVTK Slave Agent Module (Acc Quân - Headless Agent)
Represents a single slave account running in the background.
Maintains persistent connection, heartbeat keep-alive, and executes mirrored actions.
"""

import sys
import time
import random
import threading
import hashlib
import queue
from typing import Optional, Tuple, List, Dict, Any, Callable

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto
from core.config import settings
from core.device_faker import DeviceFaker
from modules.auth import AuthManager, CharacterInfo
from modules.character_registry import CharacterRegistry


class ActionTask:
    """Represents an action task in a slave agent's sequential FIFO execution queue."""
    def __init__(self, name: str, desc: str, execute_fn: Callable[['SlaveAgent'], bool], payload_data: Any = None):
        self.name = name
        self.desc = desc
        self.execute_fn = execute_fn
        self.payload_data = payload_data
        self.created_at = time.time()


class SlaveAgent:
    """Represents a headless worker that processes scripts asynchronously."""
    
    def __init__(self, index: int, username: str, password: str, slot_index: int = 0, proxy: Optional[str] = None):
        self.index = index
        self.username = username
        self.password = password
        self.slot_index = slot_index  # 0 = Slot 1, 1 = Slot 2, 2 = Slot 3
        self.proxy = proxy

        self.conn: Optional[PVTKConnection] = None
        self.character: Optional[CharacterInfo] = None
        self.characters: List[CharacterInfo] = []
        self.cur_map_id: int = 11  # Default map

        self.is_online: bool = False
        self.is_running: bool = True
        self.is_in_battle: bool = False
        self.battle_seed: int = 0
        self.last_action_time: float = time.time()
        self.latest_bag_bytes: bytes = b""
        self.last_action_desc: str = "Khởi tạo"
        self.last_battle_end_time: float = 0.0  # Timestamp of last finished battle (enforces 5s cooldown)
        self.battle_start_time: float = 0.0      # Timestamp when battle started (enforces 2.5s min battle duration)
        self.lock = threading.RLock()

        # Asynchronous FIFO Task Queue
        self.action_queue: queue.Queue = queue.Queue()
        self._queue_worker_thread: Optional[threading.Thread] = None

        self._heartbeat_thread: Optional[threading.Thread] = None
        self._is_reconnecting: bool = False

    @property
    def tag(self) -> str:
        lvl_str = f" (Lv {self.character.level})" if self.character else ""
        char_name = f"{self.character.name}{lvl_str}" if self.character else f"Slot {self.slot_index+1}"
        return f"[Acc Quân #{self.index} | {self.username} | {char_name}]"

    def trigger_auto_reconnect(self):
        """Triggers asynchronous auto-reconnect if network connection drops."""
        if not self.is_running or self._is_reconnecting:
            return
        threading.Thread(target=self._auto_reconnect_loop, daemon=True).start()

    def _auto_reconnect_loop(self):
        self._is_reconnecting = True
        self.is_online = False
        # Stop old heartbeat thread by temporarily setting is_running=False
        self.is_running = False
        time.sleep(0.5)
        self.is_running = True

        print(f"\n  \033[93m[TỰ ĐỘNG PHỤC HỒI]\033[0m Acc Quân #{self.index:02d} ({self.username}) bị ngắt kết nối mạng. Đang tự động kết nối lại...")
        for attempt in range(1, 6):
            if not self.is_running:
                break
            time.sleep(attempt * 1.5)
            try:
                ok = self.connect_and_enter(max_retries=1)
                if ok:
                    char_info = self.character.name if self.character else self.username
                    print(f"\n  \033[92m[PHỤC HỒI THÀNH CÔNG]\033[0m Acc Quân #{self.index:02d} ({self.username} | {char_info}) đã kết nối lại vào game thành công!")
                    self._is_reconnecting = False
                    return
                else:
                    print(f"  \033[90m[ĐANG THỬ LẠI {attempt}/5]\033[0m Acc Quân #{self.index:02d} ({self.username}) đang chờ kết nối lại...")
            except Exception:
                pass
        self._is_reconnecting = False
        print(f"\n  \033[91m[MẤT KẾT NỐI]\033[0m Acc Quân #{self.index:02d} ({self.username}) tạm thời mất kết nối sau 5 lần thử.")

    def connect_and_enter(self, max_retries: int = 3) -> bool:
        """
        Connects slave account to Master -> Game Server -> Enters World.
        Includes automated retry with exponential backoff if the server throttles connections.
        """
        with self.lock:
            for attempt in range(1, max_retries + 1):
                try:
                    # 1. Master Login (With controlled concurrency semaphore inside master_login)
                    login_data, err = AuthManager.master_login(
                        settings.master_host, settings.master_port,
                        self.username, self.password,
                        proxy=self.proxy
                    )
                    if not login_data:
                        time.sleep(1.0 * attempt)
                        continue

                    ukey, session_id, login_setting = login_data

                    # 2. Connect Game Server
                    self.conn = AuthManager.connect_game_server(
                        settings.game_host, settings.game_port,
                        ukey, session_id,
                        proxy=self.proxy
                    )
                    if not self.conn:
                        time.sleep(1.0 * attempt)
                        continue

                    # 3. Fetch Characters & Select Slot
                    self.characters = AuthManager.fetch_character_list(self.conn)
                    if not self.characters:
                        time.sleep(0.5)
                        continue

                    if self.slot_index < len(self.characters):
                        self.character = self.characters[self.slot_index]
                    else:
                        self.character = self.characters[0]

                    # 4. Enter World
                    entered = AuthManager.enter_character(self.conn, self.character.id, login_setting, char_info=self.character)
                    if not entered:
                        time.sleep(0.5)
                        continue

                    self.is_online = True
                    self.cur_map_id = 1
                    self.last_action_time = time.time()
                    self.sync_character_stats_from_server()
                    try:
                        self.move_to(12, 7)
                    except Exception:
                        pass
                    self.last_action_desc = f"Đã vào game (Lv {self.character.level})"

                    # 5. Launch Background Worker (Heartbeat) & FIFO Queue Consumer
                    self._start_background_worker()
                    self._start_queue_worker()
                    return True

                except Exception as e:
                    self.last_action_desc = f"Lỗi kết nối ({e})"
                    time.sleep(1.0 * attempt)

            self.is_online = False
            return False

    def _start_background_worker(self):
        """Starts background worker thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)
        self._heartbeat_thread = threading.Thread(target=self._background_worker_loop, daemon=True)
        self._heartbeat_thread.start()

    def _start_queue_worker(self):
        """Starts sequential FIFO action queue consumer thread."""
        if self._queue_worker_thread and self._queue_worker_thread.is_alive():
            return
        self._queue_worker_thread = threading.Thread(target=self._queue_worker_loop, daemon=True)
        self._queue_worker_thread.start()

    def enqueue_action(self, task: ActionTask):
        """Adds an ActionTask to this slave's sequential FIFO execution queue."""
        self.action_queue.put(task)

    def _queue_worker_loop(self):
        """Worker thread that executes queued actions sequentially one-by-one."""
        while self.is_running:
            try:
                task = self.action_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Wait if currently offline / reconnecting
            while self.is_running and (not self.is_online or not self.conn):
                time.sleep(0.5)

            if not self.is_running:
                break

            try:
                ok = task.execute_fn(self)
                char_info = f"{self.character.name} (Lv {self.character.level})" if self.character else self.username
                desc = self.last_action_desc
                remaining = self.action_queue.qsize()
                rem_str = f" \033[93m[Còn {remaining} thao tác chờ]\033[0m" if remaining > 0 else ""

                if "❌" in desc or "⚠️" in desc or "Lỗi" in desc:
                    print(f"    \033[91m-> 🤖 [Acc Quân #{self.index:02d} | {self.username} | {char_info}]:\033[0m \033[91m{desc}\033[0m{rem_str}")
                else:
                    print(f"    \033[92m-> 🤖 [Acc Quân #{self.index:02d} | {self.username} | {char_info}]:\033[0m \033[97m{desc}\033[0m{rem_str}")
            except Exception as e:
                print(f"    \033[91m-> ⚠️ [Acc Quân #{self.index:02d} | {self.username}]:\033[0m Lỗi thực thi hàng đợi: {e}")
            finally:
                self.action_queue.task_done()
                time.sleep(0.12)

    def _parse_incoming_packet(self, msg: Message):
        """Inspects incoming server packets to update character state (Level, HP, MP, EXP) in real-time."""
        if not msg or not self.character:
            return
            
        # Lưu đệm (Cache) toàn bộ dữ liệu thô của gói tin Đăng Nhập hoặc Làm Mới Hành Trang để quét Byte
        if msg.type == 0x0002 or msg.type == 0x2EE3:
            self.latest_bag_bytes = bytes(msg.data)
            
        if msg.type == 0x2AFC:  # CG_ACTOR_ATTRIBUTE_UPDATE / GS_ATTRIBUTE_UPDATE
            try:
                size = msg.getByte() if msg.hasRemaining() else 0
                for _ in range(size):
                    if not msg.hasRemaining():
                        break
                    att_type = msg.getShort()
                    att_val = msg.getInt()
                    self._apply_attribute_update(att_type, att_val)

                # Extra trailing attribute update from Server (MsgHandler.smali line 16579)
                if len(msg.data) - msg.offset >= 6:
                    extra_type = msg.getShort()
                    extra_val = msg.getInt()
                    self._apply_attribute_update(extra_type, extra_val)
            except Exception:
                pass

    def _apply_attribute_update(self, att_type: int, att_val: int):
        if not self.character:
            return
        if att_type in (0x6A, 106):  # Level Up
            old_lvl = self.character.level
            new_lvl = att_val if att_val > 0 else (old_lvl + 1)
            if new_lvl != old_lvl:
                self.character.level = new_lvl
                # Game PVTK: Thăng cấp tự động hồi phục đầy 100% HP và MP!
                max_h = getattr(self.character, "hp_max", 0)
                max_m = getattr(self.character, "mp_max", 0)
                if max_h > 0:
                    self.character.hp = max_h
                if max_m > 0:
                    self.character.mp = max_m
                print(f"\n  \033[92m🎉 [LÊN CẤP REAL-TIME]\033[0m {self.tag}: Đã thăng cấp lên \033[93mLevel {new_lvl}\033[0m! (Hồi đầy HP/MP) ★")
                try:
                    from modules.character_registry import CharacterRegistry
                    CharacterRegistry.update_character_level(settings.game_server_name, self.username, self.slot_index, new_lvl)
                except Exception:
                    pass
        elif att_type == 0x02:  # HP Delta (Player.addValue case 0x2: Tool.sumValue)
            max_hp = getattr(self.character, "hp_max", 2000)
            self.character.hp = max(1, min(max_hp, self.character.hp + att_val))
        elif att_type == 0x03:  # MP Delta (Player.addValue case 0x3: Tool.sumValue)
            max_mp = getattr(self.character, "mp_max", 1000)
            self.character.mp = max(0, min(max_mp, self.character.mp + att_val))
        elif att_type == 0x1F:  # Max HP
            self.character.hp_max = max(1, att_val)
            if self.character.hp > self.character.hp_max or self.character.hp <= 0:
                self.character.hp = self.character.hp_max
            try:
                from modules.character_registry import CharacterRegistry
                CharacterRegistry.update_character_stats(settings.game_server_name, self.username, self.slot_index, hp_max=self.character.hp_max, hp=self.character.hp)
            except Exception:
                pass
        elif att_type in (0x20, 0x1E):  # Max MP
            self.character.mp_max = max(0, att_val)
            if self.character.mp > self.character.mp_max or self.character.mp <= 0:
                self.character.mp = self.character.mp_max
            try:
                from modules.character_registry import CharacterRegistry
                CharacterRegistry.update_character_stats(settings.game_server_name, self.username, self.slot_index, mp_max=self.character.mp_max, mp=self.character.mp)
            except Exception:
                pass

    def sync_character_stats_from_server(self) -> bool:
        """Truy vấn trực tiếp Server qua Opcode 0x2B29 để lấy CHÍNH XÁC 100% HP, MP, Level, EXP thực tế."""
        if not self.conn or not self.character or not getattr(self.character, "id", None):
            return False
        try:
            req = Message(0x2B29)
            req.putInt(self.character.id)
            req.putByte(1)  # Tab 1: Chi tiết nhân vật
            res = self.conn.send_and_wait(req, expected_type=0x2B29, timeout=3.5)
            if not res or not res.hasRemaining():
                return False
            res.getByte()  # result code
            res.getInt()   # id
            res.getString() # name
            res.getString() # info
            res.getString() # title
            sex = res.getByte()
            race = res.getByte()
            job = res.getByte()
            lvl = res.getByte()
            lvl2 = res.getByte()
            res.getInt(); res.getInt() # exp2, expMax2
            res.getInt(); res.getInt(); res.getInt() # icon1, icon2, icon3
            res.getInt(); res.getInt() # setting, status
            res.getByte() # mode
            cid = res.getInt() # countryID
            if cid >= 0:
                res.getString() # countryName
                res.getByte()   # countryRank
            exp = res.getInt()
            exp_max = res.getInt()
            cur_hp = res.getInt()
            cur_mp = res.getInt()

            self.character.hp = cur_hp
            self.character.mp = cur_mp
            self.character.level = lvl
            self.character.exp = exp

            from modules.character_registry import CharacterRegistry
            CharacterRegistry.update_character_stats(
                settings.game_server_name,
                self.username,
                self.slot_index,
                hp=cur_hp,
                mp=cur_mp,
                level=lvl,
                exp=exp
            )
            return True
        except Exception:
            return False

    def _drain_and_parse_incoming(self, timeout: float = 0.03):
        """Drains socket and parses any real-time level/attribute packets from server."""
        if not self.conn:
            return
        while True:
            try:
                msg = self.conn.receive_message(timeout=timeout)
                if not msg:
                    break
                self._parse_incoming_packet(msg)
            except Exception:
                break

    def _background_worker_loop(self):
        """Passive background loop: script runner sends actions continuously, no rogue heartbeat packets needed."""
        while self.is_running:
            time.sleep(10.0)

    def auto_solve_combat(self, group_id: int = 0) -> bool:
        """Fully automated combat engine: executes attack turns and completes battle."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                # 1. Send 3 fast attack rounds (0x30D9 Plan + 0x30DA Update)
                for round_idx in range(3):
                    time.sleep(0.12)
                    plan_msg = Message(proto.CG_FIGHT_BATTLE_DOPLAN)
                    plan_msg.putByte(round_idx)
                    plan_msg.putBytes(b"\x00\x00") # Normal attack target 0
                    plan_msg.putBoolean(False)
                    self.conn.send_message(plan_msg)

                    upd_msg = Message(0x30DA)
                    upd_msg.putByte(round_idx)
                    self.conn.send_message(upd_msg)

                # 2. Send 0x30D6 victory outcome report
                time.sleep(0.12)
                hp = self.character.hp if (self.character and self.character.hp > 0) else 1000
                mp = self.character.mp if (self.character and self.character.mp > 0) else 500
                result_msg = Message(proto.CG_FIGHT_RUN_LOCALBATTLE)
                result_msg.putInt(hp)
                result_msg.putInt(mp)
                result_msg.putInt(hp)
                result_msg.putInt(mp)
                result_msg.putShort(0)
                result_msg.putShort(group_id)
                result_msg.putBoolean(False)
                result_msg.putByte(1)
                result_msg.putBytes(b"\x01\x00") # Đòn đánh thường vào mục tiêu quái 0
                self.conn.send_message(result_msg)

                # 3. Clean up socket buffer
                time.sleep(0.08)
                self._drain_and_parse_incoming(timeout=0.03)

                self.last_action_desc = "Auto đánh quái hoàn tất ★"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def accept_quest(self, npc_id: int, mission_id: int) -> bool:
        """Mirrors CG_TASK_ACCEPT (0x38A6) and inspects server response for success or failure."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                # Drain buffer
                self._drain_and_parse_incoming(timeout=0.01)
                msg = Message(proto.CG_TASK_ACCEPT)
                msg.putShort(npc_id)
                msg.putShort(mission_id)
                res = self.conn.send_and_wait(msg, expected_type=proto.CG_TASK_ACCEPT, timeout=3.0)
                
                if res and res.hasRemaining():
                    result_code = res.getByte()
                    if result_code != 0:
                        err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi {result_code}"
                        lower_err = err_msg.lower()
                        # Tự động nhận diện nhiệm vụ đã làm từ trước (Acc Clone đi trước kịch bản)
                        if result_code in (-3, -4, -9) or any(k in lower_err for k in ["không có nhiệm vụ", "đã hoàn thành", "đã nhận", "không tồn tại", "làm xong", "đã làm", "đã xong", "bản đồ lỗi", "kiểm chứng"]):
                            self.last_action_desc = f"⏩ Q #{mission_id} đã xong trước đó -> Tự động bỏ qua"
                            self.last_action_time = time.time()
                            return True
                        else:
                            self.last_action_desc = f"❌ Server từ chối nhận Q #{mission_id}: {err_msg}"
                            self.last_action_time = time.time()
                            return False
                    else:
                        self.last_action_desc = f"Nhận Q #{mission_id} (NPC #{npc_id}) THÀNH CÔNG"
                        self.last_action_time = time.time()
                        return True
                elif res:
                    self.last_action_desc = f"Nhận Q #{mission_id} (NPC #{npc_id})"
                    self.last_action_time = time.time()
                    return True
                else:
                    if not self.conn or not getattr(self.conn, "is_connected", True):
                        self.is_online = False
                        self.last_action_desc = f"⚠️ Mất kết nối tới Server khi nhận Q #{mission_id} -> Đang tự động kết nối lại..."
                        self.trigger_auto_reconnect()
                    else:
                        self.last_action_desc = f"⚠️ Không có phản hồi nhận Q #{mission_id} từ Server"
                    self.last_action_time = time.time()
                    return False
            except Exception as e:
                self.last_action_desc = f"Lỗi nhận Q: {e}"
                self.last_action_time = time.time()
                return False

    def deliver_quest(self, npc_id: int, mission_id: int, reward_item_id: int = 0) -> bool:
        """Mirrors CG_TASK_DELIVER (0x38A7) and inspects server response for success or failure."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                self._drain_and_parse_incoming(timeout=0.01)
                msg = Message(proto.CG_TASK_DELIVER)
                msg.putShort(npc_id)
                msg.putShort(mission_id)
                msg.putInt(reward_item_id)
                res = self.conn.send_and_wait(msg, expected_type=proto.CG_TASK_DELIVER, timeout=3.0)
                
                if res and res.hasRemaining():
                    result_code = res.getByte()
                    if result_code != 0:
                        err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi {result_code}"
                        lower_err = err_msg.lower()
                        # Tự động nhận diện nhiệm vụ đã trả từ trước (Acc Clone đi trước kịch bản)
                        if result_code in (-3, -4, -7, -9) or any(k in lower_err for k in ["kiểm tra npc lỗi", "không tồn tại", "đã hoàn thành", "đã giao", "làm xong", "đã làm", "đã xong", "chưa nhận", "bản đồ lỗi"]):
                            self.last_action_desc = f"⏩ Q #{mission_id} đã trả trước đó -> Tự động bỏ qua"
                            self.last_action_time = time.time()
                            return True
                        else:
                            self.last_action_desc = f"❌ Server từ chối trả Q #{mission_id}: {err_msg}"
                            self.last_action_time = time.time()
                            return False
                    else:
                        self.last_action_desc = f"Trả Q #{mission_id} (NPC #{npc_id}) THÀNH CÔNG ★"
                        self.last_action_time = time.time()
                        self._drain_and_parse_incoming()
                        return True
                elif res:
                    self.last_action_desc = f"Trả Q #{mission_id} (NPC #{npc_id}) ★"
                    self.last_action_time = time.time()
                    self._drain_and_parse_incoming()
                    return True
                else:
                    if not self.conn or not getattr(self.conn, "is_connected", True):
                        self.is_online = False
                        self.last_action_desc = f"⚠️ Mất kết nối tới Server khi trả Q #{mission_id} -> Đang tự động kết nối lại..."
                        self.trigger_auto_reconnect()
                    else:
                        self.last_action_desc = f"⚠️ Không có phản hồi trả Q #{mission_id} từ Server"
                    self.last_action_time = time.time()
                    return False
            except Exception as e:
                self.last_action_desc = f"Lỗi trả Q: {e}"
                self.last_action_time = time.time()
                return False

    def detect_current_quest_in_script(self, steps: list) -> Tuple[int, Optional[dict]]:
        """
        Kiểm tra xem nhân vật hiện tại đang ở nhiệm vụ nào trong kịch bản.
        Nếu tìm thấy nhiệm vụ đang làm hoặc nhiệm vụ kế tiếp cần nhận:
        Trả về (start_step_index, quest_info).
        Nếu không tìm thấy hoặc nhân vật mới hoàn toàn: trả về (0, None).
        """
        if not self.is_online or not self.conn:
            return 0, None

        # Trích xuất tất cả các bước nhiệm vụ (accept_quest và deliver_quest) trong kịch bản
        quest_steps = []
        seen_missions = set()
        for idx, step in enumerate(steps):
            act = step.get("action")
            if act in ("accept_quest", "deliver_quest"):
                p = step.get("params", {})
                m_id = p.get("mission_id")
                n_id = p.get("npc_id")
                if m_id is not None and n_id is not None and m_id not in seen_missions:
                    seen_missions.add(m_id)
                    quest_steps.append({
                        "step_idx": idx,
                        "mission_id": m_id,
                        "npc_id": n_id,
                        "action": act,
                        "desc": step.get("desc", f"Q #{m_id}")
                    })

        if not quest_steps:
            return 0, None

        with self.lock:
            for q in quest_steps:
                m_id = q["mission_id"]
                n_id = q["npc_id"]
                
                # Drain nhẹ
                self._drain_and_parse_incoming(timeout=0.01)

                # Test accept trước
                msg_a = Message(proto.CG_TASK_ACCEPT)
                msg_a.putShort(n_id)
                msg_a.putShort(m_id)
                res_a = self.conn.send_and_wait(msg_a, expected_type=proto.CG_TASK_ACCEPT, timeout=0.8)

                code_a = res_a.getByte() if (res_a and res_a.hasRemaining()) else None
                text_a = res_a.getString().lower() if (res_a and res_a.hasRemaining()) else ""

                # 1. Đã nhận nhiệm vụ này rồi (code -3)
                if code_a == -3 or "đã nhận" in text_a:
                    return q["step_idx"], q

                # 2. Vừa nhận thành công (code 0)
                if code_a == 0:
                    return q["step_idx"], q

                # 3. Đã hoàn thành / làm xong (code -4)
                if code_a == -4 or any(k in text_a for k in ["làm xong", "hoàn thành", "đã xong"]):
                    time.sleep(0.02)
                    continue

                # 4. Nếu chưa rõ (code -5, -7, -9, -8), test thêm deliver để kiểm tra điều kiện trả
                msg_d = Message(proto.CG_TASK_DELIVER)
                msg_d.putShort(n_id)
                msg_d.putShort(m_id)
                msg_d.putInt(0)
                res_d = self.conn.send_and_wait(msg_d, expected_type=proto.CG_TASK_DELIVER, timeout=0.8)

                code_d = res_d.getByte() if (res_d and res_d.hasRemaining()) else None
                text_d = res_d.getString().lower() if (res_d and res_d.hasRemaining()) else ""

                if code_d == -5 or "chưa đủ điều kiện" in text_d or "đã nhận" in text_d:
                    return q["step_idx"], q

                if any(k in text_d for k in ["làm xong", "hoàn thành", "đã xong"]):
                    time.sleep(0.02)
                    continue

                time.sleep(0.02)

        return 0, None

    def talk_npc(self, npc_id: int) -> bool:
        """Mirrors CG_SCENE_GETNPC (0x2917)."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_SCENE_GETNPC)
                msg.putByte(1)
                msg.putByte(npc_id)
                self.conn.send_message(msg)
                self.last_action_desc = f"Đối thoại NPC #{npc_id}"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def player_event(self, event_id: int, choose_index: int) -> bool:
        """Mirrors CG_SCENE_PLAYER_EVENT (0x290C)."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_SCENE_PLAYER_EVENT)
                msg.putInt(event_id)
                msg.putByte(choose_index)
                self.conn.send_message(msg)
                self.last_action_desc = f"Chọn sự kiện #{event_id} (Dòng {choose_index})"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def auto_sell_trash(self) -> int:
        """
        Smart Auto-Sell: Scans own inventory via CG_GET_AUTO_SELL_LIST (0x2F04)
        and batch sells all trash items via CG_AUTO_SELL (0x2F05).
        Returns number of items sold.
        """
        if not self.is_online or not self.conn:
            return 0
        with self.lock:
            try:
                self._drain_and_parse_incoming(timeout=0.01)

                # 1. Request trash item list
                get_msg = Message(proto.CG_GET_AUTO_SELL_LIST)
                res = self.conn.send_and_wait(get_msg, expected_type=proto.CG_GET_AUTO_SELL_LIST, timeout=3.5)
                if not res or not res.hasRemaining():
                    return 0

                size = res.getByte()
                if size <= 0:
                    self.last_action_desc = "Túi đồ sạch (Không có rác)"
                    return 0

                items_to_sell = []
                for _ in range(size):
                    name = res.getString()
                    qty = res.getShort()
                    grade = res.getByte()
                    slot = res.getShort()
                    item_id = res.getInt()
                    items_to_sell.append((item_id, slot, qty))

                # 2. Submit batch sell
                sell_msg = Message(proto.CG_AUTO_SELL)
                sell_msg.putByte(len(items_to_sell))
                for (item_id, slot, qty) in items_to_sell:
                    sell_msg.putInt(item_id)
                    sell_msg.putShort(slot)
                    sell_msg.putShort(qty)

                sell_res = self.conn.send_and_wait(sell_msg, expected_type=proto.CG_AUTO_SELL, timeout=4.0)
                self.last_action_desc = f"Bán Auto {len(items_to_sell)} món đồ rác"
                self.last_action_time = time.time()
                return len(items_to_sell)
            except Exception as e:
                self.last_action_desc = f"Lỗi bán rác: {e}"
                return 0

    def learn_skill(self, shop_id: int, skill_id: int, level: int = 1, is_country: bool = False) -> bool:
        """Mirrors CG_SKILL_SHOP_LEARN (0x36B2)."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_SKILL_SHOP_LEARN)
                msg.putShort(shop_id)
                msg.putInt(skill_id)
                msg.putByte(level)
                msg.putBoolean(is_country)
                res = self.conn.send_and_wait(msg, expected_type=proto.CG_SKILL_SHOP_LEARN, timeout=2.5)
                self.last_action_desc = f"Học Skill #{skill_id} (Lv {level})"
                self.last_action_time = time.time()
                return True if res else False
            except Exception:
                return False

    def set_auto_skill(self, skill_type: int, skill_id: int) -> bool:
        """Mirrors CG_SKILL_INITIATIVE_SET (0x36B9) to bind active skill to auto combat."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_SKILL_INITIATIVE_SET)
                msg.putByte(skill_type)
                msg.putInt(skill_id)
                self.conn.send_message(msg)
                self.last_action_desc = f"Đã gán Auto Skill #{skill_id} ★"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def move_to(self, gx: int, gy: int) -> bool:
        """Mirrors CG_SCENE_PLAYER_MOVING (0x2908)."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_SCENE_PLAYER_MOVING)
                msg.putByte(gx)
                msg.putByte(gy)
                self.conn.send_message(msg)
                self.last_action_desc = f"Di chuyển ({gx}, {gy})"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def change_map(self, map_id: int = 0, raw_payload: bytes = None, opcode: int = proto.CG_SCENE_GO_MAP) -> bool:
        """Mirrors map / portal teleports (0x290A, 0x290E, 0x291B, 0x291C) with exact 8-byte framing."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                target_map = map_id
                target_op = opcode or proto.CG_SCENE_GO_MAP

                if raw_payload and len(raw_payload) >= 8:
                    # Extract portal coordinates and move to them first so server validates portal collision
                    gx = raw_payload[2]
                    gy = raw_payload[3]
                    move_msg = Message(proto.CG_SCENE_PLAYER_MOVING)
                    move_msg.putByte(gx)
                    move_msg.putByte(gy)
                    self.conn.send_message(move_msg)
                    time.sleep(0.08)

                    # Forward exact verified 8-byte payload from master client
                    msg = Message(target_op, raw_payload)
                    target_map = (raw_payload[0] << 8) | raw_payload[1]
                else:
                    # Complete 8-byte structure matching MsgHandler.createJumpMapMessage(mapID, gx, gy, blockFlag)
                    msg = Message(target_op)
                    msg.putShort(map_id)
                    msg.putByte(15)   # Default gx
                    msg.putByte(20)   # Default gy
                    msg.putInt(0x40)  # Default blockFlag = 64
                    target_map = map_id

                res = self.conn.send_and_wait(msg, expected_type=target_op, timeout=3.0)

                # Drain map arrival notifications
                time.sleep(0.1)
                self._drain_and_parse_incoming(timeout=0.03)

                self.cur_map_id = target_map
                self.last_action_desc = f"Đã qua cổng sang Map #{target_map} ★"
                self.last_action_time = time.time()
                return True
            except Exception as e:
                self.last_action_desc = f"Lỗi chuyển map: {e}"
                return False

    def add_point(self, point_type: int, count: int = 1) -> bool:
        """Mirrors CG_ACTOR_ADD_POINT (0x2EE5) to add stat/potential points."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(0x2EE5)
                msg.putByte(point_type)
                msg.putInt(count)
                res = self.conn.send_and_wait(msg, expected_type=0x2EE5, timeout=2.5)
                self.last_action_desc = f"Cộng {count} điểm tiềm năng (loại #{point_type})"
                self.last_action_time = time.time()
                return True if res else False
            except Exception:
                return False

    def enter_local_battle(self) -> bool:
        """Enters local battle on the server (0x30D5) to initiate combat."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                # 1. Enforce 5-second cooldown since previous battle (nextBattleTime)
                if self.last_battle_end_time > 0:
                    elapsed = time.time() - self.last_battle_end_time
                    if elapsed < 5.0:
                        time.sleep(5.0 - elapsed)

                self._drain_and_parse_incoming(timeout=0.01)

                # 2. Gửi lệnh yêu cầu vào trận đánh (0x30D5)
                msg = Message(proto.CG_FIGHT_ENTER_LOCALBATTEL)
                self.conn.send_message(msg)

                # 3. Đánh dấu đã vào trận và tăng bộ đếm seed trận của chính nhân vật này
                self.battle_start_time = time.time()
                self.is_in_battle = True
                self.battle_seed = ((getattr(self, "battle_seed", 0)) + 1) % 10000
                if self.battle_seed == 0:
                    self.battle_seed = 1

                time.sleep(0.05)
                self._drain_and_parse_incoming()

                self.last_action_desc = "Đang trong trận đánh quái..."
                self.last_action_time = time.time()
                return True
            except Exception as e:
                self.is_in_battle = False
                self.last_action_desc = f"Lỗi vào trận: {e}"
                return False

    def fight_battle(self, group_id: int = 0, is_remote: bool = False, opcode: int = proto.CG_FIGHT_ENTER_LOCALBATTEL) -> bool:
        """Handles remote/boss combat initiation (0x30D7, 0x38B8, 0x38CF)."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                # Enforce 5-second cooldown since previous battle
                if self.last_battle_end_time > 0:
                    elapsed = time.time() - self.last_battle_end_time
                    if elapsed < 5.0:
                        time.sleep(5.0 - elapsed)

                self._drain_and_parse_incoming(timeout=0.01)

                target_gid = group_id
                if opcode == proto.CG_FIGHT_ENTER_REMOTEBATTLE or is_remote:
                    msg = Message(proto.CG_FIGHT_ENTER_REMOTEBATTLE)
                    msg.putShort(group_id)
                elif opcode in (proto.CG_TASK_TEAM_BOSS_FIGHT, proto.CG_TASK_COUNTRY_BOSS_FIGHT):
                    msg = Message(opcode)
                    if group_id > 0:
                        msg.putShort(group_id)
                elif opcode == proto.CG_FIGHT_ENTER_LOCALBATTEL:
                    msg = Message(proto.CG_FIGHT_ENTER_LOCALBATTEL)
                    # Nếu có truyền group_id (từ lệnh /battle của user), gửi kèm
                    if group_id > 0:
                        msg.putShort(group_id)
                else:
                    msg = Message(opcode)
                    if group_id > 0:
                        msg.putShort(group_id)

                self.conn.send_message(msg)
                self.battle_start_time = time.time()
                self.is_in_battle = True

                # Send remote attack rounds
                for round_idx in range(3):
                    time.sleep(0.12)
                    plan_msg = Message(proto.CG_FIGHT_BATTLE_DOPLAN)
                    plan_msg.putByte(round_idx)
                    plan_msg.putBytes(b"\x00\x00")
                    plan_msg.putBoolean(False)
                    self.conn.send_message(plan_msg)

                    upd_msg = Message(0x30DA)
                    upd_msg.putByte(round_idx)
                    self.conn.send_message(upd_msg)

                time.sleep(0.1)
                self._drain_and_parse_incoming(timeout=0.03)

                self.is_in_battle = False
                self.last_action_desc = f"Đã đánh quái (#{target_gid}) ★" if target_gid else "Đã đánh quái ★"
                self.last_action_time = time.time()
                # Đợi 5s theo quy định Server trước khi tiếp tục hành động tiếp theo
                time.sleep(5.0)
                self.last_battle_end_time = time.time()
                return True
            except Exception:
                self.is_in_battle = False
                return False

    def build_self_auto_battle_tail(self, target_gid: int, raw_payload: bytes = None, ignore_payload_hp: bool = False) -> Tuple[int, bytes]:
        """
        Nếu có payload của Trưởng và KHÔNG BỎ QUA (ignore_payload_hp=False), sao chép chính xác 100% dữ liệu hành động (tail_data).
        Nếu không, tự xây dựng dữ liệu hiệp đấu Auto độc lập 100% của chính nhân vật này.
        """
        if raw_payload and len(raw_payload) >= 22 and not ignore_payload_hp:
            round_count = raw_payload[21]
            tail_bytes = raw_payload[20:]
            return round_count, bytes(tail_bytes)

        lvl = getattr(self.character, "level", 1) if self.character else 1
        
        # 1. Trích xuất mục tiêu quái từ kịch bản hoặc mặc định target 0
        targets = [0]
        if raw_payload and len(raw_payload) >= 22:
            try:
                raw_plans = raw_payload[22:]
                offset = 0
                extracted = []
                while offset < len(raw_plans):
                    if offset + 3 > len(raw_plans):
                        break
                    plen = int.from_bytes(raw_plans[offset:offset+3], "big")
                    offset += 3
                    pdata = raw_plans[offset:offset+plen]
                    offset += plen
                    if len(pdata) >= 2:
                        extracted.append(pdata[1])
                if extracted:
                    targets = extracted
            except Exception:
                targets = [0]

        # 2. Tính số hiệp đánh tự động theo cấp độ thực tế của chính nhân vật này:
        # Cấp >= 6: 2 hiệp đánh thường là quái chết.
        # Cấp 4-5: 3 hiệp đánh thường.
        # Cấp 1-3: 4 hiệp đánh thường để đảm bảo quái nhiệm vụ chắc chắn bị tiêu diệt.
        if lvl >= 6:
            round_count = max(2, len(targets))
        elif lvl >= 4:
            round_count = max(3, len(targets))
        else:
            round_count = max(4, len(targets))
            
        round_count = min(10, round_count)

        # 3. Đóng gói tail_data: [isHavePet: 1 byte] + [round: 1 byte] + [các hiệp đánh thường]
        tail_bytes = bytearray()
        tail_bytes.append(0)           # isHavePet = False (Clone tự thân chiến đấu, không pet)
        tail_bytes.append(round_count) # Số hiệp đánh tự động

        for r_idx in range(round_count):
            tgt = targets[r_idx % len(targets)]
            # Mỗi hiệp là 1 lệnh Đánh Thường (planType=1, cursor=tgt) chuẩn Game:
            # 3 bytes độ dài (putLength=2) + 1 byte planType (1) + 1 byte cursor (tgt)
            tail_bytes.extend(b"\x00\x00\x02\x01" + bytes([tgt]))

        return round_count, bytes(tail_bytes)

    def find_slot_by_item_id(self, target_item_id: int) -> int:
        """Find the inventory slot position given an Item ID from bag bytes."""
        if not self.latest_bag_bytes:
            return -1
        from modules import bag_parser
        return bag_parser.get_slot_by_item_id(self.latest_bag_bytes, target_item_id)

    def smart_use_bag_item(self, wait_type: int, action_type: int, target_item_id: int, equip_pos: int = 0) -> bool:
        """Thực thi thông minh: Tự quét túi để tìm slot của item_id, sau đó gửi lệnh mặc đồ."""
        slot = self.find_slot_by_item_id(target_item_id)
        if slot != -1:
            action_name = "Mặc đồ" if action_type == 1 else "Sử dụng"
            print(f"[{self.tag}] 🎯 \033[92mTàu Kéo Thông Minh\033[0m: Tìm thấy vật phẩm ID {target_item_id} tại ô {slot}. Đang {action_name.lower()}...")
            return self.use_bag_item(wait_type, action_type, slot, equip_pos)
        else:
            print(f"[{self.tag}] ❌ \033[93mKHÔNG CÓ VẬT PHẨM\033[0m: Acc Quân không có vật phẩm ID {target_item_id} trong hành trang. Bỏ qua lệnh.")
            return False

    def send_local_battle_result(self, raw_0x30d6_payload: bytes = None, group_id: int = 0, ignore_payload_hp: bool = False) -> bool:
        """Submits the local battle victory result (0x30D6) using CURRENT character's parameters."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                # 1. KIỂM TRA LOGIC NGHIỆP VỤ: Nếu chưa vào trận đánh thì tự động vào trận ngay
                if not getattr(self, "is_in_battle", False):
                    self.enter_local_battle()
                    time.sleep(1.5)

                # 2. Đảm bảo trận đấu kéo dài tối thiểu 2.5s (mô phỏng thời gian đánh quái của game)
                if hasattr(self, "battle_start_time") and self.battle_start_time > 0:
                    battle_duration = time.time() - self.battle_start_time
                    if battle_duration < 2.5:
                        time.sleep(2.5 - battle_duration)

                # 3. LẤY CHỈ SỐ THỰC TẾ TRỰC TIẾP TỪ SERVER QUA 0x2B29
                # Truy vấn tức thời để đảm bảo startHP và startMP khớp 100% với dữ liệu Server
                self.sync_character_stats_from_server()

                cur_hp = getattr(self.character, "hp", 1)
                cur_mp = getattr(self.character, "mp", 0)
                hp_max = getattr(self.character, "hp_max", 0)
                mp_max = getattr(self.character, "mp_max", 0)
                if hp_max <= 0 or mp_max <= 0:
                    try:
                        from modules.character_registry import CharacterRegistry
                        stats = CharacterRegistry.get_character_stats(settings.game_server_name, self.username, self.slot_index)
                        if stats.get("hp_max", 0) > 0:
                            hp_max = stats["hp_max"]
                            self.character.hp_max = hp_max
                        if stats.get("mp_max", 0) > 0:
                            mp_max = stats["mp_max"]
                            self.character.mp_max = mp_max
                    except Exception:
                        pass

                hp_max = hp_max or 384
                mp_max = mp_max or 167

                target_gid = group_id
                if (not target_gid or target_gid <= 0) and raw_0x30d6_payload and len(raw_0x30d6_payload) >= 20:
                    target_gid = int.from_bytes(raw_0x30d6_payload[18:20], "big")

                # Mặc định tự sinh Seed (nếu tự đánh)
                self.battle_seed = ((getattr(self, "battle_seed", 0)) + 1) % 10000 or 1
                target_seed = self.battle_seed

                # 4. TÍNH TOÁN CHIẾN ĐẤU
                # NẾU CÓ DỮ LIỆU TỪ ACC TRƯỞNG -> Sử dụng Seed và Payload của Acc Trưởng để đảm bảo Server duyệt 100%
                if raw_0x30d6_payload and len(raw_0x30d6_payload) >= 20 and not ignore_payload_hp:
                    target_seed = int.from_bytes(raw_0x30d6_payload[16:18], "big")

                # CHẠY BỘ MÁY TÍNH TOÁN CHIẾN ĐẤU CHẠY NGẦM (HEADLESS BATTLE ENGINE)
                from modules.headless_battle import HeadlessBattleEngine
                start_hp, start_mp, end_hp, end_mp, seed, gid, round_count, tail_data = HeadlessBattleEngine.simulate(
                    player_hp_max=hp_max,
                    player_mp_max=mp_max,
                    group_id=target_gid,
                    seed=target_seed,
                    player_cur_hp=cur_hp,
                    player_cur_mp=cur_mp,
                    player_level=getattr(self.character, "level", 1),
                    raw_script_payload=raw_0x30d6_payload,
                    ignore_payload_hp=ignore_payload_hp
                )

                print(f"  \033[92m[TÍNH TOÁN CHIẾN ĐẤU CHẠY NGẦM]\033[0m {self.tag} (Lv {getattr(self.character, 'level', 1)}): startHP={start_hp}, startMP={start_mp} -> endHP={end_hp}, endMP={end_mp} (Quái #{gid}, Seed={seed}, Rounds={round_count})")

                # Cập nhật lại chỉ số thực tế sau trận
                self.character.hp = end_hp
                self.character.mp = end_mp
                try:
                    from modules.character_registry import CharacterRegistry
                    CharacterRegistry.update_character_stats(settings.game_server_name, self.username, self.slot_index, hp=end_hp, mp=end_mp)
                except Exception:
                    pass

                # Đóng gói 0x30D6 chuẩn Smali MsgHandler
                msg = Message(proto.CG_FIGHT_RUN_LOCALBATTLE)
                msg.putInt(start_hp)
                msg.putInt(start_mp)
                msg.putInt(end_hp)
                msg.putInt(end_mp)
                msg.putShort(seed)
                msg.putShort(gid)
                msg.putBytes(tail_data)

                res = self.conn.send_and_wait(msg, expected_type=proto.CG_FIGHT_RUN_LOCALBATTLE, timeout=3.0)

                # Đã xong trận đánh -> tắt cờ trận đấu
                self.is_in_battle = False
                self.battle_start_time = 0
                self.last_battle_end_time = time.time()

                if res:
                    res_code = res.getByte()
                    if res_code == 0:
                        print(f"       \033[92m[{self.tag}]: ★ SERVER CÔNG NHẬN CHIẾN ĐẤU THÀNH CÔNG (0x30D6: OK) ★\033[0m")
                        self.battle_error_count = 0
                        gain_money = res.getInt() if res.hasRemaining() else 0
                        gain_exp = res.getInt() if res.hasRemaining() else 0
                        exp_str = f" (+{gain_exp} EXP)" if gain_exp > 0 else ""
                        self.last_action_desc = f"Server CÔNG NHẬN tiêu diệt quái #{gid}{exp_str} ★"
                        self.last_action_time = time.time()
                        self._drain_and_parse_incoming()
                        self.last_battle_end_time = time.time()
                        return True
                    else:
                        err_msg = res.getString() if res.hasRemaining() else "Thất bại"
                        print(f"       \033[91m[{self.tag}]: ❌ Server từ chối kết quả chiến đấu: '{err_msg}'\033[0m")

                        # Quái không cùng bản đồ -> Không cần thử lại, tự động bỏ qua ngay lập tức!
                        # Quái không cùng bản đồ hoặc hết lượt -> Không cần thử lại, tự động bỏ qua ngay lập tức!
                        if any(k in err_msg.lower() for k in ["không tồn tại", "bản đồ này", "không cùng", "không tìm thấy", "hôm nay đã hết", "số lần chiến đấu"]):
                            print(f"       \033[93m[{self.tag}]: ⏩ Quái #{target_gid} không hợp lệ hoặc hết lượt -> Bỏ qua ngay không cần thử lại!\033[0m")
                            self.last_action_desc = f"⏩ Quái #{target_gid} không hợp lệ/hết lượt -> Bỏ qua"
                            self.last_action_time = time.time()
                            self.is_in_battle = False
                            self._force_exit_battle_unlocked()
                            return True

                        self.battle_error_count = getattr(self, "battle_error_count", 0) + 1

                        # CƠ CHẾ THỬ LẠI NGAY BẰNG HEADLESS BATTLE ENGINE
                        curr_seed = seed
                        for retry_idx in range(1, 101): # Tăng max retry lên 100
                            try:
                                print(f"       \033[93m[{self.tag}]: ⚠️ Server báo '{err_msg}' -> Thử lại ngay lập tức (Lần {retry_idx}/100)...\033[0m")

                                elapsed = time.time() - self.last_battle_end_time
                                if elapsed < 5.2:
                                    time.sleep(5.2 - elapsed)

                                self.conn.send_message(Message(proto.CG_FIGHT_ENTER_LOCALBATTEL))
                                self.is_in_battle = True
                                time.sleep(1.5)

                                # Đồng bộ lại chỉ số thực tế từ Server
                                self.sync_character_stats_from_server()
                                r_cur_hp = getattr(self.character, "hp", 1)
                                r_cur_mp = getattr(self.character, "mp", 0)

                                # Đổi seed và mô phỏng lại qua HeadlessBattleEngine độc lập
                                curr_seed = (curr_seed + 1) % 10000 or 1
                                self.battle_seed = curr_seed
                                r_shp, r_smp, r_ehp, r_emp, r_s, r_g, r_rc, r_tail = HeadlessBattleEngine.simulate(
                                    player_hp_max=hp_max,
                                    player_mp_max=mp_max,
                                    group_id=target_gid,
                                    seed=curr_seed,
                                    player_cur_hp=r_cur_hp,
                                    player_cur_mp=r_cur_mp,
                                    player_level=getattr(self.character, "level", 1),
                                    raw_script_payload=None,
                                    ignore_payload_hp=True
                                )

                                msg_retry = Message(proto.CG_FIGHT_RUN_LOCALBATTLE)
                                msg_retry.putInt(r_shp)
                                msg_retry.putInt(r_smp)
                                msg_retry.putInt(r_ehp)
                                msg_retry.putInt(r_emp)
                                msg_retry.putShort(r_s)
                                msg_retry.putShort(r_g)
                                msg_retry.putBytes(r_tail)

                                res_retry = self.conn.send_and_wait(msg_retry, expected_type=proto.CG_FIGHT_RUN_LOCALBATTLE, timeout=3.5)
                                self.last_battle_end_time = time.time()
                                self.is_in_battle = False

                                if res_retry and res_retry.hasRemaining():
                                    alt_code = res_retry.getByte()
                                    if alt_code >= 0:
                                        if self.character:
                                            self.character.hp = r_ehp
                                            self.character.mp = r_emp
                                        gain_money = res_retry.getInt() if res_retry.hasRemaining() else 0
                                        gain_exp = res_retry.getInt() if res_retry.hasRemaining() else 0
                                        exp_str = f" (+{gain_exp} EXP)" if gain_exp > 0 else ""
                                        self.last_action_desc = f"Server CÔNG NHẬN tiêu diệt quái #{target_gid}{exp_str} (Thử lại thành công) ★"
                                        self.last_action_time = time.time()
                                        self._drain_and_parse_incoming()
                                        self.last_battle_end_time = time.time()
                                        return True
                                    else:
                                        err_msg = res_retry.getString() if res_retry.hasRemaining() else f"Mã lỗi {alt_code}"
                                        if any(k in err_msg.lower() for k in ["không tồn tại", "bản đồ này", "không cùng", "không tìm thấy", "hôm nay đã hết", "số lần chiến đấu"]):
                                            print(f"       \033[93m[{self.tag}]: ⏩ Quái #{target_gid} không hợp lệ hoặc hết lượt -> Dừng thử lại và bỏ qua!\033[0m")
                                            self.last_action_desc = f"⏩ Quái #{target_gid} không hợp lệ/hết lượt -> Bỏ qua"
                                            self.last_action_time = time.time()
                                            self._force_exit_battle_unlocked()
                                            return True
                            except Exception as e_ret:
                                print(f"       \033[91m[{self.tag}]: Lỗi thử lại {retry_idx}: {e_ret}\033[0m")

                        self.last_action_desc = f"⚠️ Server từ chối: {err_msg}"
                        self.last_action_time = time.time()
                        self._force_exit_battle_unlocked()
                        return False
                else:
                    self._force_exit_battle_unlocked()
                    self.last_action_desc = f"⚠️ Không có phản hồi tiêu diệt quái #{target_gid} từ Server"
                    self.last_action_time = time.time()
                    return False
            except Exception as e:
                self.is_in_battle = False
                self._force_exit_battle_unlocked()
                self.last_action_desc = f"Lỗi kết thúc trận: {e}"
                return False

    def _force_exit_battle_unlocked(self):
        """Dọn dẹp buffer socket sau trận đánh mà không gửi gói tin thừa lên server."""
        try:
            time.sleep(0.05)
            self._drain_and_parse_incoming(timeout=0.02)
            self.last_battle_end_time = time.time()
        except Exception:
            pass

    def exit_battle(self) -> bool:
        """Forces exit from battle / flee / clears combat state immediately."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                self._force_exit_battle_unlocked()
                self.last_action_desc = "Đã thoát trạng thái chiến đấu"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def join_team(self, leader_id: int) -> bool:
        """Sends CG_TEAM_JOIN (0x34BE) to accept team invite or join master's party."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(proto.CG_TEAM_JOIN)
                msg.putInt(leader_id)
                self.conn.send_message(msg)
                self.last_action_desc = f"Đã vào Tổ Đội của Acc Trưởng (#{leader_id})"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def fight_local_battle(self) -> bool:
        """Backward compatible local battle."""
        return self.fight_battle(group_id=0, is_remote=False)

    def use_bag_item(self, wait_type: int, action_type: int, slot_pos: int, equip_pos: int = 0, is_upgrade: bool = False, num: int = 1) -> bool:
        """Mirrors CG_ACTOR_PLAYERBAG (0x2EE1) to use or equip item in bag."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                self._drain_and_parse_incoming(timeout=0.01)
                msg = Message(proto.CG_ACTOR_PLAYERBAG)
                msg.putByte(wait_type)
                msg.putByte(action_type)
                msg.putShort(slot_pos)
                if action_type == 1:
                    msg.putByte(equip_pos)
                msg.putBoolean(is_upgrade)
                if action_type == 8:
                    msg.putInt(num)
                res = self.conn.send_and_wait(msg, expected_type=proto.CG_ACTOR_PLAYERBAG, timeout=2.5)
                return True if res else False
            except Exception:
                return False

    def send_raw_interaction(self, opcode: int, raw_payload: bytes = b"", desc: str = "") -> bool:
        """Sends arbitrary interaction message to mirror buttons/menus/options 1:1."""
        if not self.is_online or not self.conn:
            return False
        with self.lock:
            try:
                msg = Message(opcode, raw_payload)
                self.conn.send_message(msg)
                self.last_action_desc = desc if desc else f"Tương tác (0x{opcode:04X})"
                self.last_action_time = time.time()
                return True
            except Exception:
                return False

    def close(self):
        """Closes the connection cleanly."""
        self.is_running = False
        self.is_online = False
        with self.lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
